synnefo_web_apps = [
    'synnefo.api',
    'synnefo.ui',
    'synnefo.db',
    'synnefo.logic',
    'synnefo.plankton',
    'synnefo.vmapi',
    'synnefo.helpdesk',
    'synnefo.userdata',
    'synnefo.quotas',
    'synnefo.volume',
]

synnefo_web_middleware = []
synnefo_web_context_processors = \
    ['synnefo.webproject.context_processors.cloudbar']

synnefo_static_files = {
    'synnefo.ui': 'ui/static',
    'synnefo.helpdesk': 'helpdesk',
}
