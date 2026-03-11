LOGGER_NAME = "ai_bms_pipeline"
LOGGER_FORMAT = "%(asctime)s: %(levelname)s (%(name)s:%(module)s.%(funcName)s:L%(lineno)d) - %(message)s"
DATETIME_FORMAT = "%Y-%m-%d_T%H_%M_%S%Z"
MAX_CONCURRENT_LLM_TASKS = 50

# Taken from https://www.color-hex.com/color-palette/1041937
# <a target="_blank" href="https://icons8.com/icon/7880/location">Location</a> icon by <a target="_blank" href="https://icons8.com">Icons8</a>
BAD_TO_GOOD_HEX_TO_RGB_COLORS = {
    "af1c17": (175, 28, 23),  # 0-20
    "cf7673": (207, 118, 115),  # 20-40
    "a1a38c": (161, 163, 140),  # 40-60
    "8bd7b3": (139, 215, 179),  # 60-80
    "17af68": (23, 175, 104),  # 80-100
}
