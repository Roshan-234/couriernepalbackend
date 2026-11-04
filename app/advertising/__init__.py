"""Promotions, campaigns and site notices — the serving and admin side.

The models in ``app/models/advertising.py`` set the boundary this package
works inside: Moonlight advertising its own offers on its own site. Three
consequences shape every endpoint here.

**Nothing is decided in the browser.** ``/serve`` resolves which creatives
belong on a page on the server and returns the finished list. There is no
script to embed, no auction, no call to anybody else. A frontend that
renders what it is given cannot leak a visitor to a third party, because
there is no third party.

**Targeting is geography and page only.** Country comes from the CDN header
via ``country_hint()``, never from a query parameter — a caller who could
name their own country could enumerate what every region is shown, and a
caller who could name a visitor's interests would have turned this into the
thing it is deliberately not. Because nothing is chosen from behaviour, an
advert can be served to a visitor who refused marketing cookies, which is
the whole reason the line is drawn where it is.

**The counters are public endpoints, so they are treated as hostile input.**
See ``app/advertising/throttle.py`` for what defends them and why.
"""
