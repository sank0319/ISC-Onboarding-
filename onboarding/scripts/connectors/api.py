"""API connector (web services source): ISC calls the target's REST API; there is no feed file and, normally, no
virtual appliance. See _sourcetemplate.py for the flow and how credentials are handled."""
from connectors import register
from connectors._sourcetemplate import SourceTemplate


@register
class Api(SourceTemplate):
    type = "api"
    templates_key = "apiTemplates"
    needs_cluster = False
