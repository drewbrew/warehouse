# Copyright 2013 Donald Stufft
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import time
import datetime

import pretend
import pytest

from werkzeug.exceptions import NotFound, BadRequest
from werkzeug.routing import Map

from warehouse.legacy import pypi, xmlrpc
from warehouse.packaging import urls


@pytest.mark.parametrize("content_type", [None, "text/html", "__empty__"])
def test_pypi_index(content_type):
    headers = {}

    if content_type != "__empty__":
        headers["Content-Type"] = content_type

    app = pretend.stub()
    request = pretend.stub(
        args={},
        headers=headers,
        url_adapter=pretend.stub(
            build=pretend.call_recorder(
                lambda *a, **kw: "/",
            ),
        ),
    )
    # request for /pypi with no additional request information redirects
    # to site root
    #
    resp = pypi.pypi(app, request)
    assert resp.status_code == 301
    assert resp.headers["Location"] == "/"
    assert request.url_adapter.build.calls == [
        pretend.call(
            "warehouse.views.index",
            {},
            force_external=False,
        ),
    ]


def test_pypi_route_action(monkeypatch):
    app = pretend.stub()
    request = pretend.stub(
        args={':action': 'test'},
        headers={},
    )

    _action_methods = {}
    monkeypatch.setattr(pypi, '_action_methods', _action_methods)

    @pypi.register('test')
    def test(app, request):
        test.called = True
        return 'success'

    resp = pypi.pypi(app, request)

    assert resp == 'success'
    assert test.called


def test_pypi_route_action_double(monkeypatch):
    _action_methods = {'test': None}
    monkeypatch.setattr(pypi, '_action_methods', _action_methods)

    with pytest.raises(KeyError):
        pypi.register('test')


def test_daytime(monkeypatch):
    app = pretend.stub()
    request = pretend.stub()

    monkeypatch.setattr(time, 'time', lambda: 0)

    resp = pypi.daytime(app, request)

    assert resp.response[0] == b'19700101T00:00:00\n'


@pytest.mark.parametrize(("version", "callback"), [
    (None, None),
    (None, 'yes'),
    ('1.0', 'yes'),
    ('1.0', None),
])
def test_json(monkeypatch, version, callback):
    get_project = pretend.call_recorder(lambda n: {'name': 'spam'})
    get_project_versions = pretend.call_recorder(lambda n: ['2.0', '1.0'])
    get_last_serial = pretend.call_recorder(lambda *n: 42)
    app = pretend.stub(
        config=pretend.stub(cache=pretend.stub(browser=False, varnish=False)),
        db=pretend.stub(
            packaging=pretend.stub(
                get_project=get_project,
                get_project_versions=get_project_versions,
                get_last_serial=get_last_serial,
            )
        )
    )
    request = pretend.stub(args={})
    if callback:
        request.args['callback'] = callback

    release_data = pretend.call_recorder(lambda n, v: dict(some='data'))
    release_urls = pretend.call_recorder(lambda n, v: [dict(
        some='url',
        upload_time=datetime.date(1970, 1, 1)
    )])
    all_release_urls = pretend.call_recorder(lambda *n: {
        '1.0': [dict(some='data', upload_time=datetime.date(1970, 1, 1))],
        '2.0': [dict(some='data', upload_time=datetime.date(1970, 1, 1))],
    })
    Interface = pretend.call_recorder(lambda a, r: pretend.stub(
        release_data=release_data,
        release_urls=release_urls,
        all_release_urls=all_release_urls,
    ))

    monkeypatch.setattr(xmlrpc, 'Interface', Interface)

    resp = pypi.project_json(app, request, project_name='spam',
                             version=version)

    assert get_project.calls == [pretend.call('spam')]
    assert get_project_versions.calls == [pretend.call('spam')]
    assert release_data.calls == [pretend.call('spam', version or '2.0')]
    assert release_urls.calls == [pretend.call('spam', version or '2.0')]
    assert all_release_urls.calls == [pretend.call('spam')]
    assert get_last_serial.calls == [pretend.call()]
    expected = '{"info": {"some": "data"}, ' \
        '"releases": ' \
        '{"1.0": [{"some": "data", "upload_time": "1970-01-01T00:00:00"}], ' \
        '"2.0": [{"some": "data", "upload_time": "1970-01-01T00:00:00"}]}, ' \
        '"urls": [{"some": "url", "upload_time": "1970-01-01T00:00:00"}]}'
    if callback:
        expected = '/**/ %s(%s);' % (callback, expected)
    assert resp.data == expected.encode("utf8")


def test_jsonp_invalid():
    app = pretend.stub()
    request = pretend.stub(args={'callback': 'quite invalid'})
    with pytest.raises(BadRequest):
        pypi.project_json(app, request, project_name='spam')


@pytest.mark.parametrize(("project", "version"), [
    (None, None),
    (pretend.stub(name="spam"), None),
    (pretend.stub(name="spam"), '1'),
])
def test_json_missing(monkeypatch, project, version):
    return_value = {'name': project} if project else None
    get_project = pretend.call_recorder(lambda n: return_value)
    get_project_versions = pretend.call_recorder(lambda n: [])
    app = pretend.stub(
        db=pretend.stub(
            packaging=pretend.stub(
                get_project=get_project,
                get_project_versions=get_project_versions,
            )
        )
    )
    request = pretend.stub(args={})

    with pytest.raises(NotFound):
        pypi.project_json(app, request, project_name='spam', version=version)


def test_rss(app, monkeypatch):
    now = datetime.datetime.utcnow()

    get_recently_updated = pretend.call_recorder(lambda num=10: [
        dict(name='spam', version='1.0', summary='hai spam', created=now),
        dict(name='ham', version='2.0', summary='hai ham', created=now),
        dict(name='spam', version='2.0', summary='hai spam v2', created=now),
    ])

    app.db = pretend.stub(
        packaging=pretend.stub(
            get_recently_updated=get_recently_updated,
        )
    )
    app.config = pretend.stub(
        site={"url": "http://test.server/", "name": "PyPI"},
    )

    request = pretend.stub(
        url_adapter=Map(urls.urls).bind('test.server', '/'),
    )

    resp = pypi.rss(app, request)

    assert resp.response.context == {
        "description": "package updates",
        "site": {"name": "PyPI", "url": "http://test.server/"},
        "releases": [
            {
                "url": "http://test.server/project/spam/1.0/",
                "version": "1.0",
                "name": "spam",
                "summary": "hai spam",
                "created": now,
            },
            {
                "url": "http://test.server/project/ham/2.0/",
                "version": "2.0",
                "name": "ham",
                "summary": "hai ham",
                "created": now,
            },
            {
                "url": "http://test.server/project/spam/2.0/",
                "version": "2.0",
                "name": "spam",
                "summary": "hai spam v2",
                "created": now,
            }
        ],
    }
    assert get_recently_updated.calls == [pretend.call(num=40)]


def test_packages_rss(app, monkeypatch):
    now = datetime.datetime.utcnow()

    get_recent_projects = pretend.call_recorder(lambda num=10: [
        dict(name='spam', version='1.0', summary='hai spam', created=now),
        dict(name='ham', version='2.0', summary='hai ham', created=now),
        dict(name='eggs', version='21.0', summary='hai eggs!', created=now),
    ])
    app.db = pretend.stub(
        packaging=pretend.stub(
            get_recent_projects=get_recent_projects,
        )
    )
    app.config = pretend.stub(
        site={"url": "http://test.server/", "name": "PyPI"},
    )

    request = pretend.stub(
        url_adapter=Map(urls.urls).bind('test.server', '/'),
    )

    resp = pypi.packages_rss(app, request)

    assert resp.response.context == {
        "description": "new projects",
        "site": {"name": "PyPI", "url": "http://test.server/"},
        "releases": [
            {
                "url": "http://test.server/project/spam/",
                "version": "1.0",
                "name": "spam",
                "summary": "hai spam",
                "created": now,
            },
            {
                "url": "http://test.server/project/ham/",
                "version": "2.0",
                "name": "ham",
                "summary": "hai ham",
                "created": now,
            },
            {
                "url": "http://test.server/project/eggs/",
                "version": "21.0",
                "name": "eggs",
                "summary": "hai eggs!",
                "created": now,
            },
        ],
    }
    assert get_recent_projects.calls == [pretend.call(num=40)]


def test_rss_xml_template(app, monkeypatch):
    template = app.templates.get_template('legacy/rss.xml')
    content = template.render(
        site=dict(url='http://test.server/', name="PyPI"),
        description='package updates',
        releases=[
            {
                'url': 'http://test.server/project/spam/',
                'version': u'1.0',
                'name': u'spam',
                'summary': u'hai spam',
                'created': datetime.date(1970, 1, 1),
            }, {
                'url': 'http://test.server/project/ham/',
                'version': u'2.0',
                'name': u'ham',
                'summary': u'hai ham',
                'created': datetime.date(1970, 1, 1),
            }, {
                'url': 'http://test.server/project/eggs/',
                'version': u'21.0',
                'name': u'eggs',
                'summary': u'hai eggs!',
                'created': datetime.date(1970, 1, 1),
            }
        ],
    )
    assert content == '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE rss PUBLIC "-//Netscape Communications//DTD RSS 0.91//EN" \
"http://my.netscape.com/publish/formats/rss-0.91.dtd">
<rss version="0.91">
 <channel>
  <title>PyPI Recent Package Updates</title>
  <link>http://test.server/</link>
  <description>Recent package updates at PyPI</description>
  <language>en</language>
  \n\
  <item>
    <title>spam 1.0</title>
    <link>http://test.server/project/spam/</link>
    <guid>http://test.server/project/spam/</guid>
    <description>hai spam</description>
    <pubDate>01 Jan 1970 00:00:00 GMT</pubDate>
  </item>
  \n\
  <item>
    <title>ham 2.0</title>
    <link>http://test.server/project/ham/</link>
    <guid>http://test.server/project/ham/</guid>
    <description>hai ham</description>
    <pubDate>01 Jan 1970 00:00:00 GMT</pubDate>
  </item>
  \n\
  <item>
    <title>eggs 21.0</title>
    <link>http://test.server/project/eggs/</link>
    <guid>http://test.server/project/eggs/</guid>
    <description>hai eggs!</description>
    <pubDate>01 Jan 1970 00:00:00 GMT</pubDate>
  </item>
  \n\
  </channel>
</rss>'''
