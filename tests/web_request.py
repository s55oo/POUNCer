"""Exercise the public mutation handshake in existing HTTP endpoint tests."""
import json
import urllib.request
from urllib.parse import urlsplit, urlunsplit

from pouncer.web import MUTATION_PATHS


def open_web(url, **kwargs):
    parsed = urlsplit(url)
    if parsed.path not in MUTATION_PATHS:
        return urllib.request.urlopen(url, **kwargs)
    session = urlunsplit((parsed.scheme, parsed.netloc, '/api/session', '', ''))
    with urllib.request.urlopen(session, **kwargs) as response:
        token = json.load(response)['token']
    request = urllib.request.Request(url, method='POST', headers={'X-POUNCer-Token': token})
    return urllib.request.urlopen(request, **kwargs)
