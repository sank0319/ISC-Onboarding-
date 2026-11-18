"""Linux connector: a Linux source reached over SSH through a virtual appliance (no feed file).
See _sourcetemplate.py for the flow and how credentials are handled."""
from connectors import register
from connectors._sourcetemplate import SourceTemplate


@register
class Linux(SourceTemplate):
    type = "linux"
    templates_key = "linuxTemplates"
