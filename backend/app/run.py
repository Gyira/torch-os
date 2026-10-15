"""Use the same validated listener settings as the application."""
import uvicorn
from .config import DATA_DIR, HOST, PORT, VAULT_DIR
from .security import validate_config_from_environment

if __name__ == '__main__':
    validate_config_from_environment(HOST, DATA_DIR, VAULT_DIR)
    uvicorn.run('backend.app.main:app', host=HOST, port=PORT, log_level='warning', access_log=False)
