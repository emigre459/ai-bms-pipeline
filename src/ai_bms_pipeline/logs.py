import logging
import os
import pathlib

from ai_bms_pipeline.config import DATETIME_FORMAT, LOGGER_FORMAT, LOGGER_NAME

DEFAULT_LOGGING_LEVEL = logging.WARNING


def setup_logger(
    logger_name=LOGGER_NAME,
    default_level=DEFAULT_LOGGING_LEVEL,
    filepath=None,
    align_all_loggers=False,
):
    """
    Sets up logging consistently across modules
    when imported and run at the top of a module.


    Parameters
    ----------
    logger_name: str

    default_level: int, although recommended that you
        pass logging.<LEVEL> for consistency. If you want
        functions/classes/etc. within your module to log
        messages at a level other than the default INFO,
        set it here.

    filepath: str of the form 'path/to/log.log'.
        If not None, the contents of the log will be output to
        stderr (as is typical) *and* to the file specified. Note that the
        log's storage endpoint will be dictated by other parameters in this
        function, but defaults to local disk.

    align_all_loggers: bool. If True, will force all loggers called
        from other modules to use the configuration for this one.

    Returns
    -------
    Logger object.
    """
    # We pretty much always want to write to stdout, just to be safe
    handlers = [logging.StreamHandler()]

    # Setup to write to file, if requested, too
    if filepath is not None:

        # Check that directory exists and create if it does not
        directory = os.path.split(filepath)[0]
        if not os.path.exists(directory) and directory != "":
            pathlib.Path(directory).mkdir(parents=True, exist_ok=True)

        # Translate potential relative filepath to absolute
        # Should hopefully avoid creating more than one log file when
        # logging from other modules
        absolute_filepath = os.path.abspath(filepath) + "/"

        handlers.append(logging.FileHandler(absolute_filepath, mode="a"))

    logging.basicConfig(
        format=LOGGER_FORMAT,
        level=default_level,
        datefmt=DATETIME_FORMAT,
        handlers=handlers,
        force=align_all_loggers,
    )

    return logging.getLogger(logger_name)


logger = setup_logger()
