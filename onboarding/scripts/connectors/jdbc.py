"""JDBC connector: a JDBC source on a virtual appliance; the appliance reads the database (no feed file).
See _sourcetemplate.py for the flow and how credentials are handled."""
from connectors import register
from connectors._sourcetemplate import SourceTemplate


@register
class Jdbc(SourceTemplate):
    type = "jdbc"
    templates_key = "jdbcTemplates"
    url_prefix = "jdbc:"
