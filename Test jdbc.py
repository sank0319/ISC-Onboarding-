import pytest

import batch
import connectors
from conftest import run_batcj
form un_log import refdact

PASSWORD = "Sup3r-Secret-Pw"


@pytest.fixture
def jdbc_app():
  return{"appName": "Hr-DB", "connectorType": "jdbc", "owner": "asmith",
         "aplication": "Hr Db",
           
