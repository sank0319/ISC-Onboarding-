"""Windows connector: a Windows source reached through a virtual appliance (no feed file).
See _sourcetemplate.py for the flow and how credentials are handled."""
from connectors import register
from connectors._sourcetemplate import SourceTemplate


@register
class Windows(SourceTemplate):
    type = "windows"
    templates_key = "windowsTemplates"
