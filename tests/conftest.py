import configparser
import glob
import logging
import os
import sys
import traceback

from typing import Any, Dict, Generator

import lxml.etree
import pytest

from qgis.core import Qgis, QgsApplication, QgsProject
from qgis.server import (
    QgsBufferServerRequest,
    QgsBufferServerResponse,
    QgsServer,
    QgsServerInterface,
    QgsServerRequest,
)

logging.basicConfig( stream=sys.stderr )
logging.disable(logging.NOTSET)

LOGGER = logging.getLogger('qgislogger')
LOGGER.setLevel(logging.DEBUG)

plugin_path = None


def pytest_addoption(parser) -> None:
    parser.addoption("--qgis-plugins", metavar="PATH", help="Plugin path", default=None)


def pytest_configure(config) -> None:
    global plugin_path
    plugin_path = config.getoption('qgis_plugins')


qgis_application = None


def pytest_sessionstart(session) -> None:
    """ Start qgis application
    """
    global qgis_application
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'

    # Define this in global environment
    # os.environ['QGIS_DISABLE_MESSAGE_HOOKS'] = '1'
    # os.environ['QGIS_NO_OVERRIDE_IMPORT'] = '1'

    qgis_pluginpath = os.environ.get('QGIS_PLUGINPATH', '/usr/share/qgis/python/plugins/')
    sys.path.append(qgis_pluginpath)

    qgis_application = QgsApplication([], False)
    qgis_application.setPrefixPath('/usr', True)
    qgis_application.initQgis()

    LOGGER.info("Plugins path set to  %s", plugin_path)
    sys.path.append(plugin_path)

    # Install logger hook
    install_logger_hook()


def pytest_sessionfinish(session, exitstatus) -> None:
    """ End qgis session
    """
    global qgis_application
    qgis_application.exitQgis()
    del qgis_application


class _Response:
    """ QgsBufferServerResponse wrapper
    """
    def __init__(self, resp: QgsBufferServerResponse, req: QgsBufferServerRequest) -> None:
        self._resp = resp
        self._req  = req
        self._xml = None

    @property
    def response(self):
        return self._resp

    @property
    def request(self):
        return self._req

    @property
    def xml(self) -> 'xml':
        if self._xml is None and self._resp.headers().get('Content-Type', '').find('text/xml') == 0:
            self._xml = lxml.etree.fromstring(self.content.decode('utf-8'))
        return self._xml

    @property
    def content(self) -> bytes:
        return bytes(self._resp.body())

    @property
    def status_code(self) -> int:
        return self._resp.statusCode()

    @property
    def headers(self) -> Dict[str, str]:
        return self._resp.headers()


@pytest.fixture
def client(request):
    """ Return a qgis server instance
    """
    class _Client:

        def __init__(self) -> None:

            self.datapath = request.config.rootdir.join('data')
            self.server   = QgsServer()
            self._plugins = {}

        def load_plugins(self):
            # Load plugins
            self._plugins = load_plugins(self.server.serverInterface())

        @property
        def server_interface(self):
            return self.server.serverInterface()

        def getplugin(self, name) -> Any:
            """ retourne l'instance du plugin
            """
            return self._plugins.get(name)

        def getprojectpath(self, name: str) -> str:
            return self.datapath.join(name).strpath

        def get(self, query: str, project: str = None) -> _Response:
            """ Return server response from query
            """
            request  = QgsBufferServerRequest(query, QgsServerRequest.GetMethod, {}, None)
            response = QgsBufferServerResponse()
            if project is not None and not os.path.isabs(project):
                projectpath = self.datapath.join(project)
                qgsproject  = QgsProject()
                if not qgsproject.read(projectpath.strpath):
                    raise ValueError("Error reading project '%s':" % projectpath.strpath)
            else:
                qgsproject = None
            # See https://github.com/qgis/QGIS/pull/9773
            self.server.serverInterface().setConfigFilePath(qgsproject.fileName())
            self.server.handleRequest(request, response, project=qgsproject)
            return _Response(response, request)

    return _Client()


#
# Plugins
#

def checkQgisVersion(minver: str, maxver: str) -> bool:

    def to_int(ver):
        major, *ver = ver.split('.')
        major = int(major)
        minor = int(ver[0]) if len(ver) > 0 else 0
        rev   = int(ver[1]) if len(ver) > 1 else 0
        if minor >= 99:
            minor = rev = 0
            major += 1
        if rev > 99:
            rev = 99
        return int("{:d}{:02d}{:02d}".format(major, minor, rev))

    version = to_int(Qgis.QGIS_VERSION.split('-')[0])
    minver  = to_int(minver) if minver else version
    maxver  = to_int(maxver) if maxver else version
    return minver <= version <= maxver


def find_plugins(pluginpath: str) -> Generator[str, None, None]:
    """ Load plugins
    """
    for plugin in glob.glob(os.path.join(plugin_path + "/*")):
        if not os.path.exists(os.path.join(plugin, '__init__.py')):
            LOGGER.debug("%s/__init__.py not found", plugin)
            continue

        metadatafile = os.path.join(plugin, 'metadata.txt')
        if not os.path.exists(metadatafile):
            LOGGER.debug("%s/metadata.txt not found", plugin)
            continue

        cp = configparser.ConfigParser()
        try:
            with open(metadatafile, mode='rt') as f:
                cp.read_file(f)
            if not cp['general'].getboolean('server'):
                logging.critical("%s is not a server plugin", plugin)
                continue

            minver = cp['general'].get('qgisMinimumVersion')
            maxver = cp['general'].get('qgisMaximumVersion')
        except Exception as exc:
            LOGGER.critical("Error reading plugin metadata '%s': %s", metadatafile, exc)
            continue

        if not checkQgisVersion(minver, maxver):
            LOGGER.critical((
                "Unsupported version for %s:"
                "\n MinimumVersion: %s"
                "\n MaximumVersion: %s"
                "\n Qgis version: %s"
                "\n Discarding") % (
                plugin, minver, maxver, Qgis.QGIS_VERSION.split('-')[0]))
            continue

        yield os.path.basename(plugin)


def load_plugins(serverIface: 'QgsServerInterface') -> None:
    """ Start all plugins
    """
    if not plugin_path:
        return

    server_plugins = {}

    LOGGER.info("Loading plugins from %s", plugin_path)

    for plugin in find_plugins(plugin_path):
        try:
            __import__(plugin)

            package = sys.modules[plugin]

            # Initialize the plugin
            server_plugins[plugin] = package.serverClassFactory(serverIface)
            LOGGER.info("Loaded plugin %s", plugin)
        except Exception:
            LOGGER.critical("Error loading plugin %s\n%s", plugin, traceback.format_exc())

    return server_plugins

#
# Logger hook
#


def install_logger_hook( verbose: bool = False ) -> None:
    """ Install message log hook
    """
    from qgis.core import Qgis, QgsApplication

    # Add a hook to qgis  message log
    def writelogmessage(message, tag, level):
        arg = '{}: {}'.format( tag, message )
        if level == Qgis.Warning:
            LOGGER.warning(arg)
        elif level == Qgis.Critical:
            LOGGER.error(arg)
        elif verbose:
            # Qgis is somehow very noisy
            # log only if verbose is set
            LOGGER.info(arg)

    messageLog = QgsApplication.messageLog()
    messageLog.messageReceived.connect( writelogmessage )
