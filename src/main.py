# region "debugpy"


import os
import debugpy

debugpy.listen(("0.0.0.0", 5678))

if str(os.environ.get("WAIT_FOR_DEBUG_CLIENT", "false")).lower() == "true":
    print("Waiting for debugger to attach...")
    debugpy.wait_for_client()
    print("Debugger attached.")
    debugpy.breakpoint()


# endregion
# region "import"


import datetime
import exiftool
import ffmpeg
import jmespath
import json
import logging
import re
import subprocess
import tempfile
import tika
import time

tika.TikaClientOnly = True

from tika import config
from ..run_server import run_server
from functools import cmp_to_key
from pathlib import Path
from requests.exceptions import RequestException, ReadTimeout
from tika import parser
from urllib3.exceptions import (
    HTTPError,
    ConnectionError,
    NewConnectionError,
    MaxRetryError,
)


# endregion
# region "config"


VERSION = 1
NAME = os.environ.get("NAME", "scrape")

TIKA_MIMES = {}
for attempt in range(30):
    try:
        TIKA_MIMES = set(json.loads(config.getMimeTypes()))
        break
    except:
        time.sleep(1 * attempt)

AUDIO_MIME_TYPES = {
    "audio/aac",
    "audio/flac",
    "audio/mpeg",
    "audio/mp4",
    "audio/ogg",
    "audio/wav",
    "audio/webm",
    "audio/x-wav",
    "audio/x-m4a",
    "audio/x-ms-wma",
    "audio/x-ms-wax",
    "audio/x-flac",
    "audio/x-musepack",
    "audio/x-opus",
    "audio/x-vorbis",
    "audio/x-alac",
    "application/x-pn-realaudio",
}

VIDEO_MIME_TYPES = {
    "video/x-msvideo",
    "video/x-matroska",
    "video/x-flv",
    "video/x-m4v",
    "video/x-mjpeg",
    "video/quicktime",
    "video/mp4",
    "video/mpeg",
    "video/webm",
    "video/ogg",
    "video/x-nut",
    "video/x-matroska",
    "video/x-ms-asf",
    "video/x-ms-wmv",
    "video/3gpp",
    "video/3gpp2",
    "application/x-mpegURL",
    "application/vnd.apple.mpegurl",
    "application/vnd.rn-realmedia",
    "application/vnd.rn-realmedia-vbr",
}

IMAGE_MIME_TYPES = {
    "image/jpeg",
    "image/pjpeg",
    "image/png",
    "image/gif",
    "image/bmp",
    "image/webp",
    "image/svg+xml",
    "image/x-icon",
    "image/vnd.microsoft.icon",
    "image/heif",
    "image/heic",
    "image/tiff",
    "image/x-tiff",
    "image/avif",
    "image/x-adobe-dng",
    "image/x-canon-cr2",
    "image/x-canon-crw",
    "image/x-nikon-nef",
    "image/x-nikon-nrw",
    "image/x-sony-arw",
    "image/x-sony-sr2",
    "image/x-sony-srf",
    "image/x-fuji-raf",
    "image/x-panasonic-raw",
    "image/x-panasonic-rw2",
    "image/x-olympus-orf",
    "image/x-pentax-pef",
    "image/x-sigma-x3f",
    "image/x-leica-rwl",
    "image/x-epson-erf",
    "image/x-kodak-dcr",
    "image/x-kodak-k25",
    "image/x-kodak-kdc",
    "image/x-minolta-mrw",
    "image/x-mamiya-mef",
    "image/x-hasselblad-3fr",
    "image/x-hasselblad-fff",
    "image/x-phaseone-iiq",
    "image/x-sraw",
}

ARCHIVE_MIME_TYPES = {
    "application/zip",
    "application/x-tar",
    "application/gzip",
    "application/x-bzip2",
    "application/x-7z-compressed",
    "application/x-rar-compressed",
    "application/vnd.rar",
    "application/x-ace-compressed",
    "application/x-apple-diskimage",
    "application/x-xz",
    "application/x-lzip",
    "application/x-lzma",
}

TIKA_SUPPORTED_MIMES = TIKA_MIMES - (
    AUDIO_MIME_TYPES | VIDEO_MIME_TYPES | IMAGE_MIME_TYPES | ARCHIVE_MIME_TYPES
)

EXIFTOOL_MIMES = AUDIO_MIME_TYPES | VIDEO_MIME_TYPES | IMAGE_MIME_TYPES
FFPROBE_LIBMEDIA_MIMES = AUDIO_MIME_TYPES | VIDEO_MIME_TYPES

XATTR_SUPPORTED = None


def check_xattr_supported():
    global XATTR_SUPPORTED
    if XATTR_SUPPORTED is None:
        try:
            with tempfile.NamedTemporaryFile() as temp_file:
                test_attr_name = "user.test"
                os.setxattr(temp_file.name, test_attr_name, b"test")
                os.removexattr(temp_file.name, test_attr_name)
            XATTR_SUPPORTED = True
        except (AttributeError, OSError):
            XATTR_SUPPORTED = False
            logging.warning("xattr operations are not supported on this system.")


check_xattr_supported()


# endregion
# region "parse helpers"


def get_first(dictionary, key, default=(None, None)):
    value = dictionary.get(key)
    return value[0] if isinstance(value, list) and value else default


def parse_field(d, fieldname, converter):
    value, fieldpath = get_first(d, fieldname)
    if value:
        logging.debug(f'"{fieldname}" found at "{fieldpath}" = "{value}"')
        try:
            return {fieldname: converter(str(value).strip().lower())}
        except ValueError as e:
            logging.debug(
                f'Error converting "{fieldname}" found at "{fieldpath}" = "{value}": {e}'
            )
            return None
    return None


def parse_text_field(d, fieldname):
    value, fieldpath = get_first(d, fieldname)
    if value:
        logging.debug(f'"{fieldname}" found at "{fieldpath}"')
        return {"text": value}
    return None


def parse_fraction_or_decimal(fraction_str):
    match = re.match(r"^(\d+)/(\d+)$", fraction_str.strip())
    if match:
        numerator = int(match.group(1))
        denominator = int(match.group(2))
        return numerator / denominator
    try:
        return float(fraction_str)
    except ValueError:
        raise ValueError(f"Invalid fraction or decimal format: {fraction_str}")


DESIRED_VIDEO = []
DESIRED_AUDIO = []
DESIRED_IMAGE = []
DESIRED_OTHER = []


# endregion

# region "location"


def extract_location_data(data):
    logging.debug("creating location fields")

    def from_iso6709():
        iso6709_pattern = r"(?P<latitude>[+-]\d{2}(?:\.\d+)?)(?P<longitude>[+-]\d{3}(?:\.\d+)?)(?P<altitude>[+-]\d{2,3}(?:\.\d+)?)?(?:CRS[A-Za-z0-9:_\-]+)?\/?$"
        pattern = re.compile(iso6709_pattern)
        for value, field_path in data.get("iso6709", []):
            match = pattern.match(value)
            if match:
                logging.debug(f'iso6709 gps string "{value}" found at "{field_path}"')
                result = {
                    "latitude": float(match.group("latitude")),
                    "longitude": float(match.group("longitude")),
                }
                if match.group("altitude"):
                    result["altitude"] = float(match.group("altitude"))
                    altitude = result["altitude"]
                    logging.debug(f'altitude "{altitude}" found in iso6709 string')
                return result
        return None

    def from_gps_coordinates():
        for coord, field_path in data.get("gps_coordinates", []):
            try:
                logging.debug(
                    f'coordinate tuple string "{coord}" found at "{field_path}"'
                )
                gps_coords = coord.strip().split()
                if len(gps_coords) < 2:
                    logging.debug("incomplete gps_coordinates, skipping")
                    continue
                result = {
                    "latitude": float(gps_coords[0]),
                    "longitude": float(gps_coords[1]),
                }
                if len(gps_coords) == 3:
                    result["altitude"] = float(gps_coords[2])
                    altitude = result["altitude"] = result["altitude"]
                    logging.debug(f'altitude "{altitude}" found in tuple')
                return result
            except ValueError as e:
                logging.debug("error parsing gps_coordinates")
                logging.exception(e)
                continue
        return None

    def from_gps_position():
        for position, field_path in data.get("gps_position", []):
            try:
                coords = position.strip().split()
                if len(coords) < 2:
                    logging.debug("incomplete gps_position, skipping")
                    continue
                logging.debug(
                    f'position tuple string "{position}" found at "{field_path}"'
                )
                result = {"latitude": float(coords[0]), "longitude": float(coords[1])}
                altitude_str, altitude_fieldpath = get_first(data, "altitude")
                if altitude_str:
                    logging.debug(
                        f'altitude "{altitude_str}" found at "{altitude_fieldpath}"'
                    )
                    altitude = float(str(altitude_str).strip())
                    altitude_ref_str, altitude_ref_fieldpath = get_first(
                        data, "altitude_ref"
                    )
                    if altitude_ref_str:
                        logging.debug(
                            f'altitude reference "{altitude_ref_str}" found at {altitude_ref_fieldpath}'
                        )
                        altitude_ref = str(altitude_ref_str).strip()
                        if altitude_ref != "0":
                            altitude = -altitude
                    result["altitude"] = altitude
                return result
            except Exception as e:
                logging.debug("error parsing gps_position")
                logging.exception(e)
                continue
        return None

    def from_lat_long():
        latitude_str, latitude_fieldpath = get_first(data, "latitude")
        longitude_str, longitude_fieldpath = get_first(data, "longitude")

        if (
            not latitude_str
            or not longitude_str
            or latitude_str == ""
            or longitude_str == ""
        ):
            return None

        try:
            logging.debug(f'latitude "{latitude_str}" found at "{latitude_fieldpath}"')
            logging.debug(
                f'longitude "{longitude_str}" found at "{longitude_fieldpath}"'
            )
            latitude = float(latitude_str.strip())
            longitude = float(longitude_str.strip())
            latitude_ref_str, latitude_ref_fieldpath = get_first(
                data, "latitude_ref", ("N", None)
            )
            longitude_ref_str, longitude_ref_fieldpath = get_first(
                data, "longitude_ref", ("E", None)
            )
            if latitude_ref_fieldpath:
                logging.debug(
                    f'latitude reference "{latitude_ref_str}" found at "{latitude_ref_fieldpath}"'
                )
            if longitude_ref_fieldpath:
                logging.debug(
                    f'longitude reference "{longitude_ref_str}" found at "{longitude_ref_fieldpath}"'
                )
            latitude_ref = latitude_ref_str.strip()
            longitude_ref = longitude_ref_str.strip()
            latitude = latitude if latitude_ref == "N" else -latitude
            longitude = longitude if longitude_ref == "E" else -longitude
            result = {"latitude": latitude, "longitude": longitude}

            altitude_str, altitude_fieldpath = get_first(data, "altitude")
            if altitude_str:
                logging.debug(
                    f'altitude "{altitude_str}" found at "{altitude_fieldpath}"'
                )
                altitude = float(altitude_str.strip())
                altitude_ref_str, altitude_ref_fieldpath = get_first(
                    data, "altitude_ref"
                )
                if altitude_ref_str:
                    logging.debug(
                        f'altitude reference "{altitude_ref_str}" found at {altitude_ref_fieldpath}'
                    )
                    altitude_ref = altitude_ref_str.strip()
                    if altitude_ref != "0":
                        altitude = -altitude
                result["altitude"] = altitude
            return result
        except Exception as e:
            logging.debug("error parsing latitude or longitude values")
            logging.exception(e)
            return None

    for extractor in [
        from_iso6709,
        from_gps_coordinates,
        from_gps_position,
        from_lat_long,
    ]:
        result = extractor()
        if result:
            return result

    logging.debug("location not found")
    return None


LOCATION = (
    {
        "iso6709": [
            "ffprobe.format.tags.location",
            'ffprobe.format.tags."location-eng"',
            "ffprobe.format.tags.com.apple.quicktime.location.ISO6709",
            "libmediainfo.media.track.General[0].comapplequicktimelocationiso6709",
            "libmediainfo.media.track.General[0].xyz",
        ],
        "gps_coordinates": ['exiftool."QuickTime:GPSCoordinates"'],
        "altitude": ['exiftool."Composite:GPSAltitude"', "exiftool.GPSAltitude"],
        "altitude_ref": [
            'exiftool."Composite:GPSAltitudeRef"',
            "exiftool.GPSAltitudeRef",
        ],
        "gps_position": ['exiftool."Composite:GPSPosition"'],
        "latitude": ['exiftool."Composite:GPSLatitude"', "exiftool.GPSLatitude"],
        "longitude": ['exiftool."Composite:GPSLongitude"', "exiftool.GPSLongitude"],
        "latitude_ref": [
            'exiftool."Composite:GPSLatitudeRef"',
            "exiftool.GPSLatitudeRef",
        ],
        "longitude_ref": [
            'exiftool."Composite:GPSLongitudeRef"',
            "exiftool.GPSLongitudeRef",
        ],
    },
    extract_location_data,
)


DESIRED_VIDEO.append(LOCATION)
DESIRED_AUDIO.append(LOCATION)
DESIRED_IMAGE.append(LOCATION)


# endregion
# region "timestamp"


def parse_timestamp(components):
    logging.debug("parsing datetime from components to unix timestamp with precision")
    year = None
    month = 1
    day = 1
    hour = 0
    minute = 0
    second = 0
    microsecond = 0
    precision_order = [
        "year",
        "month",
        "day",
        "hour",
        "minute",
        "second",
        "microsecond",
    ]
    precision_levels = {
        "year": 0,
        "month": 1,
        "day": 2,
        "hour": 3,
        "minute": 4,
        "second": 5,
        "microsecond": 6,
    }
    highest_precision = -1
    for comp in precision_order:
        if comp in components and components[comp] is not None:
            val = str(components[comp]).strip()
            try:
                if comp == "year":
                    year = int(val)
                    if year < 1:
                        raise ValueError(f"invalid year: '{val}'")
                elif comp == "month":
                    month = int(val)
                    if not 1 <= month <= 12:
                        raise ValueError(f"invalid month: '{val}'")
                elif comp == "day":
                    day = int(val)
                    if not 1 <= day <= 31:
                        raise ValueError(f"invalid day: '{val}'")
                elif comp == "hour":
                    hour = int(val)
                    if not 0 <= hour <= 23:
                        raise ValueError(f"invalid hour: '{val}'")
                elif comp == "minute":
                    minute = int(val)
                    if not 0 <= minute <= 59:
                        raise ValueError(f"invalid minute: '{val}'")
                elif comp == "second":
                    second = int(val)
                    if not 0 <= second <= 59:
                        raise ValueError(f"invalid second: '{val}'")
                elif comp == "microsecond":
                    if val.startswith("."):
                        val = "0" + val
                    elif "." not in val:
                        val = "0." + val
                    fractional_seconds = float(val)
                    microsecond = int(round(fractional_seconds * 1_000_000))
                    if not 0 <= microsecond < 1_000_000:
                        raise ValueError(f"invalid microsecond '{val}'")
                highest_precision = precision_levels[comp]
            except ValueError as ve:
                logging.debug(f"error in {comp}: {ve}")
                raise ValueError(f"invalid value for {comp}: '{val}'. {ve}")
        else:
            if comp == "year":
                logging.debug("missing required year component")
                raise ValueError("year is required")
    try:
        dt = datetime.datetime(
            year=year,
            month=month,
            day=day,
            hour=hour,
            minute=minute,
            second=second,
            microsecond=microsecond,
            tzinfo=None,
        )
        logging.debug(f"datetime parsed as iso string {dt.isoformat()}")
    except ValueError as ve:
        logging.debug(f"datetime creation failed: {ve}")
        raise ValueError(f"failed to create datetime object: {ve}")
    is_real_offset = False
    if "offset" in components and components["offset"] is not None:
        is_real_offset = True
        offset_str = components["offset"].strip()
        logging.debug(f"using included offset component {offset_str}")
        try:
            offset_seconds = parse_offset_string(offset_str)
            offset_delta = datetime.timedelta(seconds=offset_seconds)
        except ValueError as ve:
            logging.debug(f"invalid offset: {ve}")
            raise ValueError(f"invalid offset value: '{offset_str}'. {ve}")
    else:
        system_timezone = datetime.datetime.now().astimezone().tzinfo
        if system_timezone is None:
            logging.debug('no system timezone, defaulted to "UTC"')
            offset = datetime.timezone.utc
            offset_delta = offset.utcoffset(dt)
        else:
            offset_delta = system_timezone.utcoffset(dt)
            logging.debug(
                f'defaulted to system timezone "{datetime.datetime.now().astimezone().tzname()}"'
            )
    if offset_delta is not None:
        offset_seconds = int(offset_delta.total_seconds())
    else:
        offset_seconds = 0
    dt = dt.replace(tzinfo=datetime.timezone(offset_delta))
    dt_utc = dt.astimezone(datetime.timezone.utc)
    unix_timestamp = dt_utc.timestamp()
    return unix_timestamp, highest_precision, offset_seconds, is_real_offset


def parse_offset_string(offset_str):
    offset_str = offset_str.strip()
    if offset_str.upper() == "Z":
        return 0
    sign = 1
    if offset_str.startswith("-"):
        sign = -1
        offset_str = offset_str[1:]
    elif offset_str.startswith("+"):
        offset_str = offset_str[1:]
    for delimiter in [":", "_"]:
        offset_str = offset_str.replace(delimiter, "")
    if len(offset_str) == 2:
        offset_str += "00"
    if len(offset_str) != 4 or not offset_str.isdigit():
        logging.debug("invalid offset format")
        raise ValueError("offset must be in '+HHMM' format")
    hours = int(offset_str[:2])
    minutes = int(offset_str[2:])
    if not 0 <= hours <= 23 or not 0 <= minutes <= 59:
        logging.debug("offset hours or minutes out of range")
        raise ValueError("offset hours must be 0-23 and minutes 0-59")
    offset_seconds = sign * (hours * 3600 + minutes * 60)
    return offset_seconds


def month_str_to_int(month_str):
    month_str = month_str.strip().lower()
    month_dict = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }
    return month_dict.get(month_str[:3], None)


RIGID_FILEPATH_DATETIME_REGEX_PATTERN = r"""(?x)
    (?P<year>\d{4})
    (?:
        [ _:-]*
        (?P<month>0[1-9]|1[0-2])
        (?:
            [ _:-]*
            (?P<day>0[1-9]|[12]\d|3[01])
            (?:
                (?:[ _:-]*|T)
                (?P<hour>0[0-9]|1[0-9]|2[0-3])
                (?:
                    [ _:]*
                    (?P<minute>[0-5]\d)
                    (?:
                        [ _:]*
                        (?P<second>[0-5]\d)
                        (?:
                            [ ._:]*
                            (?P<microsecond>\d+)
                        )?
                    )?
                )?
                (?:
                    [ _]*
                    (?P<offset>Z|[+-](?:0[0-9]|1[0-9]|2[0-3])[_:]?[0-5]\d)?
                )?
            )?
        )?
    )?
    (?P<type>(?:!|&)~)
    (?!/.*)
"""

RELAXED_FILEPATH_DATETIME_REGEX_PATTERN = r"""(?ix)  # Verbose and case-insensitive mode

# ----------------------- Date and Time Patterns -----------------------

(?:
    # Dates that allow time
    (?:
        # ISO 8601 Date: YYYY-MM-DD or YYYYMMDD
        (?P<date_iso>
            (?P<year_iso>\d{4})
            (?P<sep_date>[-_./: ]?)  # Optional separator
            (?P<month_iso>0[1-9]|1[0-2])
            (?P=sep_date)
            (?P<day_iso>0[1-9]|[12]\d|3[01])
        )
        |
        # US Date Format: MM-DD-YYYY or MMDDYYYY
        (?P<date_us>
            (?P<month_us>0[1-9]|1[0-2])
            (?P=sep_date)
            (?P<day_us>0[1-9]|[12]\d|3[01])
            (?P=sep_date)
            (?P<year_us>\d{4})
        )
        |
        # Month Name Format: Month DD, YYYY
        (?P<date_month_name>
            \b(?P<monthname>
                Jan(?:uary)?|
                Feb(?:ruary)?|
                Mar(?:ch)?|
                Apr(?:il)?|
                May|
                Jun(?:e)?|
                Jul(?:y)?|
                Aug(?:ust)?|
                Sep(?:t(?:ember)?)?|
                Oct(?:ober)?|
                Nov(?:ember)?|
                Dec(?:ember)?
            )\b
            \.?\s+
            (?P<dayname>\d{1,2}),?\s+
            (?P<yearname>\d{4})
        )
        |
        # New Pattern: DD-Mon-YYYY
        (?P<date_dmy>
            (?P<day_dmy>0[1-9]|[12]\d|3[01])
            (?P<sep_dmy>[-_./: ])
            (?P<month_dmy>
                Jan(?:uary)?|
                Feb(?:ruary)?|
                Mar(?:ch)?|
                Apr(?:il)?|
                May|
                Jun(?:e)?|
                Jul(?:y)?|
                Aug(?:ust)?|
                Sep(?:t(?:ember)?)?|
                Oct(?:ober)?|
                Nov(?:ember)?|
                Dec(?:ember)?
            )
            (?P=sep_dmy)
            (?P<year_dmy>\d{4})
        )
        |
        # New Pattern: YYYY/MonthName/DD
        (?P<date_ymd>
            (?P<year_ymd>\d{4})
            (?P<sep_ymd>[-_./: ])
            (?P<month_ymd>
                Jan(?:uary)?|
                Feb(?:ruary)?|
                Mar(?:ch)?|
                Apr(?:il)?|
                May|
                Jun(?:e)?|
                Jul(?:y)?|
                Aug(?:ust)?|
                Sep(?:t(?:ember)?)?|
                Oct(?:ober)?|
                Nov(?:ember)?|
                Dec(?:ember)?
            )
            (?P=sep_ymd)
            (?P<day_ymd>0[1-9]|[12]\d|3[01])
        )
        |
        # New Pattern: DD-MM-YYYY (numeric)
        (?P<date_dmy_numeric>
            (?P<day_dmy_n>0[1-9]|[12]\d|3[01])
            (?P<sep_dmy_n>[-_./: ]?)
            (?P<month_dmy_n>0[1-9]|1[0-2])
            (?P=sep_dmy_n)
            (?P<year_dmy_n>\d{4})
        )
        |
        # New Pattern: YYYY-DD-MM (numeric)
        (?P<date_ydm_numeric>
            (?P<year_ydm_n>\d{4})
            (?P<sep_ydm_n>[-_./: ]?)
            (?P<day_ydm_n>0[1-9]|[12]\d|3[01])
            (?P=sep_ydm_n)
            (?P<month_ydm_n>0[1-9]|1[0-2])
        )
        |
        # New Pattern: MM-DD-YYYY (numeric)
        (?P<date_mdy_numeric>
            (?P<month_mdy_n>0[1-9]|1[0-2])
            (?P<sep_mdy_n>[-_./: ]?)
            (?P<day_mdy_n>0[1-9]|[12]\d|3[01])
            (?P=sep_mdy_n)
            (?P<year_mdy_n>\d{4})
        )
    )
    # Optional Time Patterns
    (?:
        [T ./_:-]*?
        (?:
            (?P<time12>
                # 12-hour format with AM/PM (AM/PM is now mandatory)
                (?P<hour12>0?[1-9]|1[0-2])
                (?P<sep_time2>[-_./: ]?)(?P<minute12>[0-5]\d)
                (?:
                    (?P=sep_time2)(?P<second12>[0-5]\d)
                    (?:[.,]?(?P<millisecond12>\d+))?
                )?
                [-_./ ]*(?P<ampm>[AP][M])  # AM/PM is required
                (?P<timezone12>
                    Z|
                    [+-](?:[01]\d|2[0-3])[ _:]?(?::?[0-5]\d)?
                )?
            )
            |
            (?P<time24>
                # 24-hour format (won't match if AM/PM is present)
                (?P<hour24>0\d|1\d|2[0-3])
                (?P<sep_time1>[-_./: ]?)(?P<minute24>[0-5]\d)
                (?:
                    (?P=sep_time1)(?P<second24>[0-5]\d)
                    (?:[.,]?(?P<millisecond24>\d+))?
                )?
                (?P<timezone24>
                    Z|
                    [+-](?:[01]\d|2[0-3])[ _:]?(?::?[0-5]\d)?
                )?
                (?!\s*[AP][M])  # Negative lookahead to ensure AM/PM is not present
            )
        )
    )?
    |
    # Dates that do NOT allow time
    (?:
        # Year-Month Numeric or Month Name
        (?P<year_month>
            (?P<year_ym>(?:19\d{2}|20\d{2}))
            (?P<sep_ym>[-_./: ])
            (?P<month_ym>
                0[1-9]|1[0-2]|
                Jan(?:uary)?|
                Feb(?:ruary)?|
                Mar(?:ch)?|
                Apr(?:il)?|
                May|
                Jun(?:e)?|
                Jul(?:y)?|
                Aug(?:ust)?|
                Sep(?:t(?:ember)?)?|
                Oct(?:ober)?|
                Nov(?:ember)?|
                Dec(?:ember)?
            )
        )
        |
        # Month Name and Year
        (?P<month_year>
            \b(?P<month_my>
                Jan(?:uary)?|
                Feb(?:ruary)?|
                Mar(?:ch)?|
                Apr(?:il)?|
                May|
                Jun(?:e)?|
                Jul(?:y)?|
                Aug(?:ust)?|
                Sep(?:t(?:ember)?)?|
                Oct(?:ober)?|
                Nov(?:ember)?|
                Dec(?:ember)?
            )\b
            \.?\s*
            (?P<year_my>(?:19\d{2}|20\d{2}))
        )
        |
        # Year Only
        (?P<year_only>
            \b(?P<year_only_value>(?:19\d{2}|20\d{2}))\b
        )
    )
)
"""

METADATA_DATETIME_REGEX_PATTERN = r"""(?x)
    (?P<year>\d{4})
    (?:
        [ _:-]*
        (?P<month>0[1-9]|1[0-2])
        (?:
            [ _:-]*
            (?P<day>0[1-9]|[12]\d|3[01])
            (?:
                (?:[ _:-]*|T)
                (?P<hour>0[0-9]|1[0-9]|2[0-3])
                (?:
                    [ _:]*
                    (?P<minute>[0-5]\d)
                    (?:
                        [ _:]*
                        (?P<second>[0-5]\d)
                        (?:
                            [ ._:]*
                            (?P<microsecond>\d+)
                        )?
                    )?
                )?
                (?:
                    [ _]*
                    (?P<offset>Z|[+-](?:0[0-9]|1[0-9]|2[0-3])[_:]?[0-5]\d)?
                )?
            )?
        )?
    )?
"""

rigid_filepath_datetime_regex_pattern = re.compile(
    RIGID_FILEPATH_DATETIME_REGEX_PATTERN, re.VERBOSE | re.IGNORECASE
)
relaxed_filepath_datetime_regex_pattern = re.compile(
    RELAXED_FILEPATH_DATETIME_REGEX_PATTERN, re.VERBOSE | re.IGNORECASE
)
metadata_datetime_regex_pattern = re.compile(
    METADATA_DATETIME_REGEX_PATTERN, re.VERBOSE | re.IGNORECASE
)


def scrape_datetime_from_filepath_rigid(file_path):
    logging.debug("finding rigid datetime in file path")
    components = [
        "year",
        "month",
        "day",
        "hour",
        "minute",
        "second",
        "microsecond",
        "offset",
    ]
    match = rigid_filepath_datetime_regex_pattern.search(file_path)
    if match:
        logging.debug(
            f'rigid datetime found in file path "{match}" "{match.groupdict()}"'
        )
        typestr = match.group("type")
        if typestr == "!~":
            type_variable = "known"
        elif typestr == "&~":
            type_variable = "estimate"
        else:
            logging.debug("no rigid datetime found in file path")
            return None, None
        return {
            comp: match.group(comp) for comp in components if match.group(comp)
        }, type_variable
    logging.debug("no rigid datetime found in file path")
    return None, None


def scrape_datetime_from_filepath_relaxed(file_path):
    logging.debug("finding relaxed datetime in file path")
    components = [
        "year",
        "month",
        "day",
        "hour",
        "minute",
        "second",
        "microsecond",
        "offset",
    ]
    max_components = -1
    matched_components = {}
    for match in relaxed_filepath_datetime_regex_pattern.finditer(file_path):
        logging.debug(
            f'relaxed datetime found in file path "{match}" "{match.groupdict()}"'
        )
        temp_components = {}
        date_formats = [
            "date_iso",
            "date_us",
            "date_month_name",
            "date_dmy",
            "date_ymd",
            "date_dmy_numeric",
            "date_ydm_numeric",
            "date_mdy_numeric",
            "year_month",
            "month_year",
            "year_only",
        ]
        for date_format in date_formats:
            if match.group(date_format):
                logging.debug(
                    f"matched relaxed datetime with {date_format} date format"
                )
                if date_format == "date_iso":
                    temp_components.update(
                        {
                            "year": match.group("year_iso"),
                            "month": match.group("month_iso"),
                            "day": match.group("day_iso"),
                        }
                    )
                elif date_format == "date_us":
                    temp_components.update(
                        {
                            "year": match.group("year_us"),
                            "month": match.group("month_us"),
                            "day": match.group("day_us"),
                        }
                    )
                elif date_format == "date_month_name":
                    temp_components.update(
                        {
                            "year": match.group("yearname"),
                            "day": match.group("dayname"),
                            "month": month_str_to_int(match.group("monthname")),
                        }
                    )
                elif date_format == "date_dmy":
                    temp_components.update(
                        {
                            "year": match.group("year_dmy"),
                            "day": match.group("day_dmy"),
                            "month": month_str_to_int(match.group("month_dmy")),
                        }
                    )
                elif date_format == "date_ymd":
                    temp_components.update(
                        {
                            "year": match.group("year_ymd"),
                            "day": match.group("day_ymd"),
                            "month": month_str_to_int(match.group("month_ymd")),
                        }
                    )
                elif date_format == "date_dmy_numeric":
                    temp_components.update(
                        {
                            "year": match.group("year_dmy_n"),
                            "day": match.group("day_dmy_n"),
                            "month": match.group("month_dmy_n"),
                        }
                    )
                elif date_format == "date_ydm_numeric":
                    temp_components.update(
                        {
                            "year": match.group("year_ydm_n"),
                            "day": match.group("day_ydm_n"),
                            "month": match.group("month_ydm_n"),
                        }
                    )
                elif date_format == "date_mdy_numeric":
                    temp_components.update(
                        {
                            "year": match.group("year_mdy_n"),
                            "day": match.group("day_mdy_n"),
                            "month": match.group("month_mdy_n"),
                        }
                    )
                elif date_format == "year_month":
                    month = match.group("month_ym")
                    temp_components.update(
                        {
                            "year": match.group("year_ym"),
                            "month": (
                                month if month.isdigit() else month_str_to_int(month)
                            ),
                        }
                    )
                elif date_format == "month_year":
                    month = match.group("month_my")
                    temp_components.update(
                        {
                            "year": match.group("year_my"),
                            "month": month_str_to_int(month),
                        }
                    )
                elif date_format == "year_only":
                    temp_components["year"] = match.group("year_only_value")
                if (
                    temp_components.get("year")
                    and temp_components.get("month")
                    and temp_components.get("day")
                ):
                    break
        if match.group("time12"):
            logging.debug("matched related datetime with 12-hour time format")
            hour = int(match.group("hour12"))
            ampm = match.group("ampm", "").upper()
            if ampm == "PM" and hour != 12:
                hour += 12
            elif ampm == "AM" and hour == 12:
                hour = 0
            temp_components.update(
                {
                    "hour": str(hour),
                    "minute": match.group("minute12"),
                    "second": match.group("second12"),
                    "microsecond": match.group("millisecond12"),
                    "offset": match.group("timezone12"),
                }
            )
        elif match.group("time24"):
            temp_components.update(
                {
                    "hour": match.group("hour24"),
                    "minute": match.group("minute24"),
                    "second": match.group("second24"),
                    "microsecond": match.group("millisecond24"),
                    "offset": match.group("timezone24"),
                }
            )
        num_components = sum(
            temp_components.get(comp) is not None for comp in components
        )
        if num_components >= max_components:
            max_components = num_components
            matched_components = temp_components
    if not matched_components:
        logging.debug("no relaxed datetime found in file path")
        return None
    return matched_components


def datetime_to_components(dt):
    return (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second, dt.microsecond)


def compare_dt_tuples(dt_tuple1, dt_tuple2):
    """Custom comparator that compares datetimes with precision and is_offset_real."""
    dt1, precision1, offset1, is_offset_real1, _ = dt_tuple1
    dt2, precision2, offset2, is_offset_real2, _ = dt_tuple2

    # Determine the minimum precision
    min_precision = min(precision1, precision2)
    components1 = datetime_to_components(dt1)[: min_precision + 1]
    components2 = datetime_to_components(dt2)[: min_precision + 1]

    if components1 != components2:
        # Components differ up to min_precision
        return -1 if components1 < components2 else 1
    else:
        # Components are equal up to min_precision
        if precision1 != precision2:
            # Prefer the datetime with higher precision
            return -1 if precision1 > precision2 else 1

        # Check if year, month, day are equal
        ymd1 = datetime_to_components(dt1)[:3]
        ymd2 = datetime_to_components(dt2)[:3]
        if ymd1 == ymd2:
            # Compare the remaining components (hour, minute, second, microsecond, offset)
            rest1 = datetime_to_components(dt1)[3:] + (offset1,)
            rest2 = datetime_to_components(dt2)[3:] + (offset2,)
            if rest1 != rest2:
                if is_offset_real1 != is_offset_real2:
                    # Prefer the datetime where is_offset_real is True
                    return -1 if is_offset_real1 else 1
        else:
            # Check for day difference of ±1 and prefer real offset
            if ymd1[:2] == ymd2[:2]:  # Same year and month
                day_difference = abs(ymd1[2] - ymd2[2])
                if day_difference == 1:
                    if is_offset_real1 != is_offset_real2:
                        # Prefer the datetime where is_offset_real is True
                        return -1 if is_offset_real1 else 1
        # All components are equal or is_offset_real is the same
        return 0


def extract_timestamp(data):
    logging.debug("creating creation_date field")
    mtime, _ = get_first(data, "fs.mtime")
    logging.debug(
        f'mtime read as "{mtime}" local iso "{datetime.datetime.fromtimestamp(mtime, datetime.datetime.now().astimezone().tzinfo)}"'
    )
    parsed_datetimes = []
    offset_time, offset_time_fieldpath = get_first(data, "offset_time")
    if offset_time:
        logging.debug(f'utc offset "{offset_time}" found at "{offset_time_fieldpath}"')
    filepath_rigid_timestamp, _ = get_first(
        data, "filepath.creation_date_rigid.timestamp"
    )
    filepath_rigid_precision_level, _ = get_first(
        data, "filepath.creation_date_rigid.precision_level"
    )
    filepath_rigid_utc_offset_seconds, _ = get_first(
        data, "filepath.creation_date_rigid.utc_offset_seconds"
    )
    filepath_rigid_is_offset_real, _ = get_first(
        data, "filepath.creation_date_rigid.is_offset_real"
    )
    filepath_rigid_type, _ = get_first(data, "filepath.creation_date_rigid.type")
    filepath_relaxed_timestamp, _ = get_first(
        data, "filepath.creation_date_relaxed.timestamp"
    )
    filepath_relaxed_precision_level, _ = get_first(
        data, "filepath.creation_date_relaxed.precision_level"
    )
    filepath_relaxed_utc_offset_seconds, _ = get_first(
        data, "filepath.creation_date_relaxed.utc_offset_seconds"
    )
    filepath_relaxed_is_offset_real, _ = get_first(
        data, "filepath.creation_date_relaxed.is_offset_real"
    )
    if filepath_rigid_timestamp:
        logging.debug(f"adding rigid datetime from filename for consideration")
        ts = datetime.datetime.fromtimestamp(
            filepath_rigid_timestamp,
            datetime.timezone(
                datetime.timedelta(seconds=filepath_rigid_utc_offset_seconds)
            ),
        )
        parsed_datetimes.append(
            (
                ts,
                filepath_rigid_precision_level,
                filepath_rigid_utc_offset_seconds,
                filepath_rigid_is_offset_real,
                "rigid datetime from file path",
            )
        )
    for key in [
        "iso8601",
        "datetime_with_microsecond",
        "datetime_with_offset",
        "datetime_without_offset",
    ]:
        found_values = data.get(key, [])
        for value, field_path in found_values:
            try:
                value = value.strip()
                match = metadata_datetime_regex_pattern.match(value)
                if match:
                    logging.debug(
                        f'matched metadata field "{field_path}" with value "{value}" as valid datetime "{match.groupdict()}"'
                    )
                    parsed_components = match.groupdict()
                    if (
                        not "offset" in parsed_components
                        or parsed_components["offset"] is None
                    ):
                        parsed_components["offset"] = offset_time
                    timestamp, highest_precision, offset_seconds, is_offset_real = (
                        parse_timestamp(parsed_components)
                    )
                    dt = datetime.datetime.fromtimestamp(
                        timestamp,
                        datetime.timezone(datetime.timedelta(seconds=offset_seconds)),
                    )
                    parsed_datetimes.append(
                        (
                            dt,
                            highest_precision,
                            offset_seconds,
                            is_offset_real,
                            field_path,
                        )
                    )
                    logging.debug(
                        f'parsed as "{dt.isoformat()}" with precision "{highest_precision}"'
                    )
            except Exception as e:
                logging.warning(
                    f"malformed datetime string '{value}' at '{field_path}': {e}"
                )
    timestamp_data = {}
    used_relaxed = False
    if not parsed_datetimes and filepath_relaxed_timestamp:
        logging.debug(
            f"no reliable datetime found, considering relaxed datetime from filepath"
        )
        used_relaxed = True
        ts = datetime.datetime.fromtimestamp(
            filepath_relaxed_timestamp,
            datetime.timezone(
                datetime.timedelta(seconds=filepath_relaxed_utc_offset_seconds)
            ),
        )
        parsed_datetimes.append(
            (
                ts,
                filepath_relaxed_precision_level,
                filepath_relaxed_utc_offset_seconds,
                filepath_relaxed_is_offset_real,
                "relaxed datetime from file path",
            )
        )
    if parsed_datetimes:
        sorted_parsed_datetimes = sorted(
            parsed_datetimes, key=cmp_to_key(compare_dt_tuples)
        )
        best_dt, best_precision, best_offset, best_is_real, file_path = (
            sorted_parsed_datetimes[0]
        )
        logging.debug(
            f'creation_date found at "{file_path}" "{best_dt.isoformat()}" as earliest with highest precision "{best_precision}"'
        )
        if best_precision < 6 and mtime:
            try:
                timestamp = float(mtime)
                mtime_dt = datetime.datetime.fromtimestamp(
                    timestamp,
                    datetime.timezone(datetime.timedelta(seconds=best_offset)),
                )
                if all(
                    getattr(best_dt, comp, None) == getattr(mtime_dt, comp, None)
                    for comp in [
                        "year",
                        "month",
                        "day",
                        "hour",
                        "minute",
                        "second",
                        "microsecond",
                    ]
                ):
                    best_dt = mtime_dt
                    logging.debug(
                        f'enhanced datetime precision with mtime to "{best_dt.isoformat()}"'
                    )
            except Exception as e:
                logging.exception(f"error processing mtime for best datetime: {e}")
        timestamp_data["creation_date"] = best_dt.timestamp()
        timestamp_data["creation_date_precision"] = best_precision
        if used_relaxed:
            timestamp_data["creation_date_is_inferred"] = used_relaxed
        timestamp_data["creation_date_offset_seconds"] = (
            best_dt.utcoffset().total_seconds()
        )
        if not best_is_real:
            timestamp_data["creation_date_offset_is_inferred"] = not best_is_real
        if filepath_rigid_type:
            timestamp_data["creation_date_special_filepath_type"] = filepath_rigid_type
    elif mtime:
        try:
            timestamp_data["creation_date"] = float(mtime)
            logging.debug(
                f'selected mtime as only available creation_date "{timestamp_data}"'
            )
        except Exception as e:
            logging.exception(f"error converting mtime to float: {e}")
    return timestamp_data


TIMESTAMP = (
    {
        "iso8601": [
            "ffprobe.format.tags.creation_time",
            "ffprobe.streams.video[0].tags.creation_time",
            "ffprobe.streams.audio[0].tags.creation_time",
            "ffprobe.format.tags.com.apple.quicktime.creationdate",
        ],
        "datetime_with_microsecond": [
            'exiftool."Composite:SubSecCreateDate"',
            'exiftool."Composite:SubSecDateTimeOriginal"',
        ],
        "datetime_with_offset": [
            'exiftool."XML:CreationDateValue"',
            'exiftool."QuickTime:CreationDate"',
        ],
        "datetime_without_offset": [
            'exiftool."EXIF:CreateDate"',
            'exiftool."EXIF:DateTimeOriginal"',
            'exiftool."QuickTime:CreateDate"',
            'exiftool."QuickTime:MediaCreateDate"',
            'exiftool."QuickTime:TrackCreateDate"',
        ],
        "offset_time": [
            'exiftool."EXIF:OffsetTime"',
            'exiftool."EXIF:OffsetTimeOriginal"',
            'exiftool."EXIF:OffsetTimeDigitized"',
        ],
        "fs.mtime": ["fs.mtime"],
        "filepath.creation_date_rigid.timestamp": [
            "filepath.creation_date_rigid.timestamp"
        ],
        "filepath.creation_date_rigid.precision_level": [
            "filepath.creation_date_rigid.precision_level"
        ],
        "filepath.creation_date_rigid.utc_offset_seconds": [
            "filepath.creation_date_rigid.utc_offset_seconds"
        ],
        "filepath.creation_date_rigid.is_offset_real": [
            "filepath.creation_date_rigid.is_offset_real"
        ],
        "filepath.creation_date_rigid.type": ["filepath.creation_date_rigid.type"],
        "filepath.creation_date_relaxed.timestamp": [
            "filepath.creation_date_relaxed.timestamp"
        ],
        "filepath.creation_date_relaxed.precision_level": [
            "filepath.creation_date_relaxed.precision_level"
        ],
        "filepath.creation_date_relaxed.utc_offset_seconds": [
            "filepath.creation_date_relaxed.utc_offset_seconds"
        ],
        "filepath.creation_date_relaxed.is_offset_real": [
            "filepath.creation_date_relaxed.is_offset_real"
        ],
    },
    extract_timestamp,
)


DESIRED_VIDEO.append(TIMESTAMP)
DESIRED_AUDIO.append(TIMESTAMP)
DESIRED_IMAGE.append(TIMESTAMP)
DESIRED_OTHER.append(TIMESTAMP)


# endregion
# region "audio bit rate"


AUDIO_BIT_RATE = (
    {
        "audio_bit_rate": [
            "ffprobe.format.bit_rate",
            "libmediainfo.media.track.General[0].OverallBitRate",
            "libmediainfo.media.track.Audio[0].BitRate",
            "ffprobe.streams.audio[0].bit_rate",
        ]
    },
    lambda d: parse_field(d, "audio_bit_rate", int),
)


DESIRED_AUDIO.append(AUDIO_BIT_RATE)


# endregion
# region "video audio bit rate"


VIDEO_AUDIO_BIT_RATE = (
    {
        "video_bit_rate": [
            "libmediainfo.media.track.Audio[0].BitRate",
            "ffprobe.streams.audio[0].bit_rate",
        ]
    },
    lambda d: parse_field(d, "video_bit_rate", int),
)


DESIRED_VIDEO.append(VIDEO_AUDIO_BIT_RATE)


# endregion
# region "audio channels"


AUDIO_CHANNELS = (
    {
        "audio_channels": [
            "ffprobe.streams.audio[0].channels",
            "libmediainfo.media.track.Audio[0].Channels",
            'exiftool."XML:AudioFormatNumOfChannel"',
            'exiftool."QuickTime:AudioChannels"',
        ]
    },
    lambda d: parse_field(d, "audio_channels", int),
)


DESIRED_VIDEO.append(AUDIO_CHANNELS)
DESIRED_AUDIO.append(AUDIO_CHANNELS)


# endregion
# region "audio duration"


AUDIO_DURATION = (
    {
        "duration": [
            "ffprobe.format.duration",
            "ffprobe.streams.audio[0].duration",
            'exiftool."XML:DurationValue"',
        ]
    },
    lambda d: parse_field(d, "duration", float),
)


DESIRED_AUDIO.append(AUDIO_DURATION)


# endregion
# region "audio codec"


AUDIO_CODEC = (
    {
        "audio_codec": [
            "ffprobe.streams.audio[0].codec_name",
            "libmediainfo.media.track.Audio[0].Format",
            'exiftool."QuickTime:AudioFormat"',
        ]
    },
    lambda d: parse_field(d, "audio_codec", str),
)


DESIRED_VIDEO.append(AUDIO_CODEC)
DESIRED_AUDIO.append(AUDIO_CODEC)


# endregion
# region "audio sample rate"


AUDIO_SAMPLE_RATE = (
    {
        "audio_sample_rate": [
            "libmediainfo.media.track.Audio[0].SamplingRate",
            "ffprobe.streams.audio[0].sample_rate",
            'exiftool."QuickTime:AudioSampleRate"',
        ]
    },
    lambda d: parse_field(d, "audio_sample_rate", int),
)


DESIRED_VIDEO.append(AUDIO_SAMPLE_RATE)
DESIRED_AUDIO.append(AUDIO_SAMPLE_RATE)


# endregion
# region "device make"


DEVICE_MAKE = (
    {
        "device_make": [
            'exiftool."EXIF:Make"',
            'exiftool."XML:DeviceManufacturer"',
            'exiftool."QuickTime:Make"',
            'exiftool."QuickTime:AndroidManufacturer"',
            "ffprobe.format.tags.device_make",
            "ffprobe.format.tags.com.android.manufacturer",
            "libmediainfo.media.track.General[0].comandroidmanufacturer",
            'exiftool."ICC_Profile:DeviceManufacturer"',
        ]
    },
    lambda d: parse_field(d, "device_make", str),
)


DESIRED_VIDEO.append(DEVICE_MAKE)
DESIRED_AUDIO.append(DEVICE_MAKE)
DESIRED_IMAGE.append(DEVICE_MAKE)


# endregion
# region "device model"


DEVICE_MODEL = (
    {
        "device_model": [
            'exiftool."EXIF:Model"',
            'exiftool."XML:DeviceModelName"',
            'exiftool."QuickTime:Model"',
            'exiftool."QuickTime:AndroidModel"',
            "ffprobe.format.tags.device_model",
            "ffprobe.format.tags.com.android.model",
            "libmediainfo.media.track.General[0].comandroidmodel",
            'exiftool."ICC_Profile:DeviceModel"',
        ]
    },
    lambda d: parse_field(d, "device_model", str),
)


DESIRED_VIDEO.append(DEVICE_MODEL)
DESIRED_AUDIO.append(DEVICE_MODEL)
DESIRED_IMAGE.append(DEVICE_MODEL)


# endregion
# region "video duration"

VIDEO_DURATION = (
    {
        "duration": [
            "ffprobe.format.duration",
            "ffprobe.streams.video[0].duration",
            'exiftool."XML:DurationValue"',
        ]
    },
    lambda d: parse_field(d, "duration", float),
)


DESIRED_VIDEO.append(VIDEO_DURATION)


# endregion
# region "camera lens make"


CAMERA_LENS_MAKE = (
    {"camera_lens_make": ['exiftool."EXIF:LensMake"']},
    lambda d: parse_field(d, "camera_lens_make", str),
)


DESIRED_VIDEO.append(CAMERA_LENS_MAKE)
DESIRED_IMAGE.append(CAMERA_LENS_MAKE)


# endregion
# region "camera lens model"


CAMERA_LENS_MODEL = (
    {
        "camera_lens_model": [
            'exiftool."EXIF:LensModel"',
            'exiftool."QuickTime:CameraLensModel"',
        ]
    },
    lambda d: parse_field(d, "camera_lens_model", str),
)


DESIRED_VIDEO.append(CAMERA_LENS_MODEL)
DESIRED_IMAGE.append(CAMERA_LENS_MODEL)


# endregion
# region "height"


HEIGHT = (
    {
        "height": [
            'exiftool."EXIF:ExifImageHeight"',
            "ffprobe.streams.video[0].height",
            'exiftool."QuickTime:ImageHeight"',
            'exiftool."File:ImageHeight"',
        ]
    },
    lambda d: parse_field(d, "height", int),
)


DESIRED_VIDEO.append(HEIGHT)
DESIRED_IMAGE.append(HEIGHT)


# endregion
# region "width"


WIDTH = (
    {
        "width": [
            'exiftool."EXIF:ExifImageWidth"',
            "ffprobe.streams.video[0].width",
            'exiftool."QuickTime:ImageWidth"',
            'exiftool."File:ImageWidth"',
        ]
    },
    lambda d: parse_field(d, "width", int),
)


DESIRED_VIDEO.append(WIDTH)
DESIRED_IMAGE.append(WIDTH)


# endregion
# region "video bit rate"


VIDEO_BIT_RATE = (
    {
        "video_bit_rate": [
            "libmediainfo.media.track.Video[0].BitRate",
            "ffprobe.streams.video[0].bit_rate",
        ]
    },
    lambda d: parse_field(d, "video_bit_rate", int),
)


DESIRED_VIDEO.append(VIDEO_BIT_RATE)


# endregion
# region "video codec"


VIDEO_CODEC = (
    {"video_codec": ["ffprobe.streams.video[0].codec_name"]},
    lambda d: parse_field(d, "video_codec", str),
)


DESIRED_VIDEO.append(VIDEO_CODEC)


# endregion
# region "video frame rate"


VIDEO_FRAME_RATE = (
    {
        "video_frame_rate": [
            "ffprobe.streams.video[0].r_frame_rate",
            "libmediainfo.media.track.General[0].FrameRate",
            "libmediainfo.media.track.Video[0].FrameRate",
        ]
    },
    lambda d: parse_field(d, "video_frame_rate", parse_fraction_or_decimal),
)


DESIRED_VIDEO.append(VIDEO_FRAME_RATE)


# endregion
# region "text"


TEXT = (
    {
        "text": [
            'tika."X-TIKA:content"',
        ]
    },
    lambda d: parse_text_field(d, "text"),
)


DESIRED_OTHER.append(TEXT)


# endregion

# region "jmespath"


def jmespath_search_with_fallbacks(data, search_dict):
    field_results = {}
    for field, query_list in search_dict.items():
        result = None
        field_results[field] = []
        for query in query_list:
            try:
                result = jmespath.search(query, data)
                if result is not None:
                    field_results[field].append((result, str(query).replace('"', "")))
            except Exception as e:
                logging.exception(e)
                continue
        if not field_results[field]:
            field_results.pop(field)
    return field_results if field_results else None


def jmespath_search_with_shaped_list(data, list):
    results = {}
    for search_dict, parser in list:
        try:
            search_results = jmespath_search_with_fallbacks(data, search_dict)
            if search_results:
                if parser:
                    parsed_results = parser(search_results)
                    if parsed_results:
                        results.update(parsed_results)
                else:
                    for k, v in search_results.items():
                        if len(v) > 0:
                            results[k] = v[0]
        except Exception as e:
            logging.exception(e)
            continue
    return results


# endregion

# region "exiftool"


def scrape_with_exiftool(file_path):
    try:
        with exiftool.ExifToolHelper() as et:
            logging.debug(f"{NAME} exiftool start")
            metadata = et.get_metadata(file_path)[0]
            logging.debug(f"{NAME} exiftool done")
            return metadata
    except Exception as e:
        logging.warning(f"{NAME} exiftool failed: {e}")
        return None


# endregion
# region "ffprobe"


def scrape_with_ffprobe(file_path):
    try:
        logging.debug(f"{NAME} ffprobe start")
        metadata = ffmpeg.probe(file_path)
        logging.debug(f"{NAME} ffprobe done")
        streams_by_type = {}
        for stream in metadata.get("streams", []):
            stream_type = stream.get("codec_type", "unknown")
            if stream_type not in streams_by_type:
                streams_by_type[stream_type] = []
            streams_by_type[stream_type].append(stream)
        metadata["streams"] = streams_by_type
        return metadata
    except Exception as e:
        stderr_output = e.stderr.decode("utf-8") if e.stderr else ""
        error_message = (
            stderr_output.strip().split("\n")[-1]
            if stderr_output
            else "Unknown ffprobe error"
        )
        logging.warning(f'{NAME} ffprobe failed: "{error_message}"')
        return None


# endregion
# region "libmediainfo"


def scrape_with_libmediainfo(file_path):
    try:
        logging.debug(f"{NAME} mediainfo start")
        result = subprocess.run(
            ["mediainfo", "--Output=JSON", file_path],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        logging.debug(f"{NAME} mediainfo done")
        metadata = json.loads(result.stdout)
        media = metadata.get("media", {})
        tracks_by_type = {}
        for track in media.get("track", []):
            track_type = track.get("@type", "unknown")
            if track_type not in tracks_by_type:
                tracks_by_type[track_type] = []
            tracks_by_type[track_type].append(track)
        media["track"] = tracks_by_type
        metadata["media"] = media
        return metadata
    except Exception as e:
        logging.warning(f"{NAME} mediainfo failed: {e}")
        return None


# endregion
# region "file system"


def scrape_with_os(file_path):
    try:
        path = Path(file_path)
        stat_info = path.stat()

        scrape = {
            "atime": stat_info.st_atime,
            "ctime": stat_info.st_ctime,
            "dev": stat_info.st_dev,
            "gid": stat_info.st_gid,
            "ino": stat_info.st_ino,
            "mode": stat_info.st_mode,
            "mtime": stat_info.st_mtime,
            "nlink": stat_info.st_nlink,
            "size": stat_info.st_size,
            "uid": stat_info.st_uid,
        }

        try:
            if XATTR_SUPPORTED:
                for attr_name in os.listxattr(file_path):
                    try:
                        data = os.getxattr(file_path, attr_name)
                        scrape[f"xattr.{attr_name}"] = data.decode(
                            "utf-8", errors="ignore"
                        )
                    except Exception as e:
                        logging.exception(f"Failed to read xattr {attr_name}")
        except Exception as e:
            logging.exception(f"Failed to read xattrs")

        return scrape
    except Exception as e:
        logging.warning(f"{NAME} os stat failed: {e}")
        return None


# endregion
# region "tika"


def scrape_with_tika(file_path):
    try:
        logging.debug(f"{NAME} tika start")
        parsed = parser.from_file(file_path, requestOptions={"timeout": 60})
        logging.debug(f"{NAME} tika done")
        metadata = parsed["metadata"]
        if parsed["content"]:
            metadata["X-TIKA:content"] = parsed["content"]
        return metadata
    except ReadTimeout as e:
        logging.warning(f"{NAME} tika failed: request timed out")
        return None
    except ReadTimeout as e:
        logging.warning(f"{NAME} tika failed: request timed out")
        return None
    except (RequestException, HTTPError) as e:
        raise e
    except Exception as e:
        cause = e
        if e.__cause__:
            cause = e.__cause__
        logging.warning(f"{NAME} tika failed {type(e).__name__}: {cause}")
        return None


# endregion
# region "file path"


def scrape_file_path(file_path):
    logging.debug("scrape file path")
    result = {}

    try:
        rigid_datetime_components, rigid_type = scrape_datetime_from_filepath_rigid(
            file_path
        )
        if rigid_datetime_components:
            logging.debug(f'found rigid datetime set "{rigid_datetime_components}"')
            timestamp, highest_precision, offset_seconds, is_offset_real = (
                parse_timestamp(rigid_datetime_components)
            )
            result["creation_date_rigid"] = {
                "timestamp": timestamp,
                "precision_level": highest_precision,
                "utc_offset_seconds": offset_seconds,
                "is_offset_real": is_offset_real,
                "type": rigid_type,
            }
            logging.debug(
                f'parsed rigid datetime set as "{result["creation_date_rigid"]}"'
            )
    except Exception as e:
        logging.warning(f"{NAME} file path rigid date time failed: {e}")

    try:
        relaxed_datetime_components = scrape_datetime_from_filepath_relaxed(file_path)
        if relaxed_datetime_components:
            logging.debug(f'found relaxed datetime set "{relaxed_datetime_components}"')
            timestamp, highest_precision, offset_seconds, is_offset_real = (
                parse_timestamp(relaxed_datetime_components)
            )
            result["creation_date_relaxed"] = {
                "timestamp": timestamp,
                "precision_level": highest_precision,
                "utc_offset_seconds": offset_seconds,
                "is_offset_real": is_offset_real,
            }
            logging.debug(
                f'parsed relaxed datetime set as "{result["creation_date_relaxed"]}"'
            )
    except Exception as e:
        logging.warning(f"{NAME} file path relaxed date time failed: {e}")

    logging.debug(f'scraped metadata from file path "{result}"')
    return result


# endregion

# region "hello"


def hello():
    return {
        "name": NAME,
        "filterable_attributes": ["_geo"]
        + [
            f"{NAME}.{x}"
            for x in [
                "altitude",
                "audio_bit_depth",
                "audio_bit_rate",
                "audio_channels",
                "audio_codec",
                "audio_sample_rate",
                "camera_lens_make",
                "camera_lens_model",
                "creation_date",
                "creation_date_precision",
                "creation_date_is_inferred",
                "creation_date_offset_seconds",
                "creation_date_offset_is_inferred",
                "creation_date_special_filepath_type",
                "device_make",
                "device_model",
                "duration",
                "height",
                "video_bit_rate",
                "video_codec",
                "video_frame_rate",
                "width",
            ]
        ],
        "sortable_attributes": ["_geo"]
        + [
            f"{NAME}.{x}"
            for x in [
                "duration",
                "creation_date",
            ]
        ],
    }


# endregion
# region "check/run"


def check(file_path, document, metadata_dir_path):
    version = None
    version_path = metadata_dir_path / "version.json"
    if version_path.exists():
        with open(version_path, "r") as file:
            version = json.load(file)
    if (
        version
        and version.get("file_path") == file_path
        and version.get("version") == VERSION
    ):
        return False
    return True


def run(file_path, document, metadata_dir_path):
    version_path = metadata_dir_path / "version.json"
    metadata_path = metadata_dir_path / "metadata.json"
    txt_path = metadata_dir_path / "text.txt"

    metadata = {}
    metadata["fs"] = scrape_with_os(file_path)
    metadata["filepath"] = scrape_file_path(file_path)

    if document["type"] in EXIFTOOL_MIMES:
        metadata["exiftool"] = scrape_with_exiftool(file_path)
    if document["type"] in FFPROBE_LIBMEDIA_MIMES:
        metadata["ffprobe"] = scrape_with_ffprobe(file_path)
        metadata["libmediainfo"] = scrape_with_libmediainfo(file_path)
    if document["type"] in TIKA_SUPPORTED_MIMES:
        metadata["tika"] = scrape_with_tika(file_path)

    if document["type"] in VIDEO_MIME_TYPES:
        desired_fields = jmespath_search_with_shaped_list(metadata, DESIRED_VIDEO)
    elif document["type"] in AUDIO_MIME_TYPES:
        desired_fields = jmespath_search_with_shaped_list(metadata, DESIRED_AUDIO)
    elif document["type"] in IMAGE_MIME_TYPES:
        desired_fields = jmespath_search_with_shaped_list(metadata, DESIRED_IMAGE)
    else:
        desired_fields = jmespath_search_with_shaped_list(metadata, DESIRED_OTHER)

    document[NAME] = {}

    if desired_fields["latitude"] and desired_fields["longitude"]:
        # this is how meilisearch likes gps data
        document["_geo"] = {
            "lat": desired_fields["latitude"],
            "lng": desired_fields["longitude"],
        }
        del desired_fields["latitude"]
        del desired_fields["longitude"]

    for key, value in (desired_fields or {}).items():
        document[NAME][key] = value

    with open(version_path, "w") as file:
        logging.debug(f"write {version_path}")
        json.dump(
            {"version": VERSION, "file_path": file_path},
            file,
            indent=4,
            separators=(", ", ": "),
        )

    with open(metadata_path, "w") as file:
        logging.debug(f"write {metadata_path}")
        json.dump(metadata, file, indent=4, separators=(", ", ": "))

    if (
        "tika" in metadata
        and metadata["tika"] is not None
        and "X-TIKA:content" in metadata["tika"]
    ):
        with open(txt_path, "w") as file:
            logging.debug(f"write {txt_path}")
            file.write(metadata["tika"]["X-TIKA:content"])

    return document


# endregion


if __name__ == "__main__":
    run_server(hello, check, run)
