#!/usr/bin/python

import datetime, htmlentitydefs, logging, os, re, sys, urllib, urllib2

sys.path = [os.path.join(os.path.dirname(__file__), "lib")] + sys.path
import redis

URLBASE = "http://epetitions.direct.gov.uk"
URLS = {
    "OPEN_PETITIONS_RECENT_FIRST": "/petitions.html?state=open&sort=created&order=desc",
}

def _unescape(text):
    """
    Removes HTML or XML character references and entities from a text string.
    
    Taken from http://effbot.org/zone/re-sub.htm#unescape-html
    
    @param text The HTML (or XML) source text.
    @return The plain text, as a Unicode string, if necessary.
    """
    def fixup(m):
        text = m.group(0)
        if text[:2] == "&#":
            # character reference
            try:
                if text[:3] == "&#x":
                    return unichr(int(text[3:-1], 16))
                else:
                    return unichr(int(text[2:-1]))
            except ValueError:
                pass
        else:
            # named entity
            try:
                text = unichr(htmlentitydefs.name2codepoint[text[1:-1]])
            except KeyError:
                pass
        return text # leave as is
    
    return re.sub("&#?\w+;", fixup, text)

def _extract(regex, text, tolerate_missing=False):
    mo = re.search(regex, text)
    if not mo:
        if tolerate_missing:
            return None
        else:
            raise Exception("Could not find /%s/ in '%s'" % (regex, text))
    groups = mo.groups()
    if len(groups) < 1:
        raise Exception("Regular expression /%s/ has no match groups?", regex)
    return _unescape(groups[0].strip())

def _int(s):
    return int(re.sub(r'\D', '', s))

class PetitionScraper(object):
    def _petition_links(self, html):
        for mo in re.finditer(r'<td class="name"><a href="([^"]+)" class="text_link">([^<]+)', html):
            link, title = mo.groups()
            yield _unescape(link) #, _unescape(title.strip())
    
    def _petitions(self, html):
        for link in self._petition_links(html):
            yield self._petition(link)
    
    def _parse_petition_html(self, html):
        return {
            "title": _extract(r"<h1>([^<]+)", html),
            "description": _extract(r"(?s)<p class='description'>(.+?)</p>", html),
            "department": _extract(r"<p class='department'>Responsible department: ([^<]+)", html),
            
            "signature_count": _int(_extract(r'<dd class="signature_count">([\d,]+)</dd>', html)),
            "created_by": _extract(r'<dd class="created_by">([^<]+)</dd>', html),
            "closing_date": datetime.datetime.strptime(
                _extract(r'<dd class="closing_date">(\d\d/\d\d/\d\d\d\d)</dd>', html),
                "%d/%m/%Y"
            ),
        }

    def _petition(self, link):
        """Fetch a petition, and return the data as a dict.
        """
        url = URLBASE + link
        f = urllib2.urlopen(url)
        try:
            if f.code != 200:
                raise Exception("%s returned status %d" % (url, f.code))
            r = self._parse_petition_html(f.read().decode("utf-8"))
            r["link"] = link
            return r
        finally:
            f.close()

    def fetch_petitions(self, path=URLS["OPEN_PETITIONS_RECENT_FIRST"]):
        while True:
            url = URLBASE + path
            logging.info("Fetching %s", url)
            f = urllib2.urlopen(url)
            try:
                if f.code != 200:
                    raise Exception("%s returned status %d" % (url, f.code))
                
                html = f.read().decode("utf-8")
            finally:
                f.close()
            
            for petition in self._petitions(html):
                yield petition
            
            next_link = _extract(r'<li class="next_link">\s*<a href="([^"]*)', html, tolerate_missing=True)
            if next_link:
                path = next_link
            else:
                break

class PetitionStore(object):
    def __init__(self, r):
        self.r = r
        self.scraper = PetitionScraper()
    
    def get_new_petitions(self):
        for petition in self.scraper.fetch_petitions():
            logging.info("Loaded %s (%s)", petition["link"], petition["title"])
            if self.r.exists(petition["link"]):
                logging.info("%s already exists", petition["link"])
                break
            self.r.lpush("oldest-first", petition["link"])
            for k, v in petition.items():
                self.r.hset(petition["link"], k, v)

def main():
    logging.basicConfig(level=logging.INFO)
    
    petition_store = PetitionStore(redis.Redis())
    petition_store.get_new_petitions()

if __name__ == "__main__":
    main()
