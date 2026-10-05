"""Small helpers that hide differences between QGIS / Qt versions."""
import configparser
import os
import traceback

from qgis.core import Qgis, QgsFieldConstraints, QgsFields, QgsMessageLog

TAG = "Subtypes and Domains"


def constraint_expression():
    value = getattr(QgsFieldConstraints, "ConstraintExpression", None)
    if value is None:  # QGIS 4 / scoped enums
        value = QgsFieldConstraints.Constraint.ConstraintExpression
    return value


def origin_expression():
    value = getattr(QgsFields, "OriginExpression", None)
    if value is None:  # QGIS 4 / scoped enums
        value = Qgis.FieldOrigin.Expression
    return value


def _level(name):
    value = getattr(Qgis, name, None)
    if value is None:
        value = getattr(Qgis.MessageLevel, name)
    return value


def log(message, warning=False):
    QgsMessageLog.logMessage(message, TAG, _level("Warning" if warning else "Info"))


def log_exception(context):
    log("%s\n%s" % (context, traceback.format_exc()), warning=True)


def plugin_version():
    parser = configparser.ConfigParser()
    try:
        parser.read(os.path.join(os.path.dirname(__file__), "metadata.txt"), encoding="utf-8")
        return parser["general"].get("version", "?")
    except (KeyError, configparser.Error, OSError):
        return "?"
