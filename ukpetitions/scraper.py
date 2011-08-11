#!/usr/bin/python
# -*- encoding: utf-8 -*-

import datetime, htmlentitydefs, logging, os, re, sys, time, urllib, urllib2

sys.path = [os.path.join(os.path.dirname(__file__), os.path.pardir, "lib")] + sys.path
import redis

URLBASE = "http://epetitions.direct.gov.uk"
URLS = {
    "OPEN_PETITIONS_RECENT_FIRST": "/petitions.html?state=open&sort=created&order=desc",
    "OPEN_PETITIONS_MOST_VOTES_FIRST": "/petitions.html?state=open&sort=count&order=desc",
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

MAX_BACKOFF_SECS = 60
MAX_RETRIES = 10
def urlfetch(url):
    logging.info("Fetching %s", url)
    backoff, retries = 1, 0
    while retries < MAX_RETRIES:
        
        # The HTTPError object behaves just like a response object;
        # we don’t want to distinguish between success and error responses
        # so dramatically, so just get the response (whatever it is) into
        # a local variable 'f'.
        try:
            f = urllib2.urlopen(url)
        
        except urllib2.HTTPError, f:
            pass
        
        except urllib2.URLError, e:
            logging.info("Error: %s. Sleeping for %d seconds", e, backoff)
            time.sleep(backoff)
            backoff, retries = min(backoff * 2, MAX_BACKOFF_SECS), retries + 1
            continue
        
        try:
            if f.code == 200:
                return f.read().decode("utf-8")
            elif f.code == 502 or f.code == 504:
                # 502 "Bad gateway" often indicates a service under load.
                # 504 "Gateway timeout" is similar.
                # Back off and try again.
                logging.info("502 error. Sleeping for %d seconds", backoff)
                time.sleep(backoff)
                backoff, retries = min(backoff * 2, MAX_BACKOFF_SECS), retries + 1
                continue
            
            raise Exception("%s returned status %d" % (url, f.code))
        finally:
            f.close()

    raise Exception("Giving up after retrying %d times." % (MAX_RETRIES))

class PetitionScraper(object):
    def _petition_links(self, html):
        for mo in re.finditer(r'<td class="name"><a href="([^"]+)" class="text_link">', html):
            yield _unescape(mo.group(1))
    
    def _petition_vote_counts(self, html):
        for mo in re.finditer(r'<td class="name"><a href="([^"]+)" class="text_link">.*?<td class="sig_count">([\d,]+)</td>', html):
            yield mo.groups()
    
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
        r = self._parse_petition_html(urlfetch(URLBASE + link))
        r["link"] = link
        return r

    def _each_serp(self, query_path):
        while True:
            html = urlfetch(URLBASE + query_path)
            yield html
            
            next_link = _extract(r'<li class="next_link">\s*<a href="([^"]*)', html, tolerate_missing=True)
            if next_link:
                path = next_link
            else:
                break
    
    def fetch_petitions(self, path=URLS["OPEN_PETITIONS_RECENT_FIRST"]):
        for html in self._each_serp(path):
            for link in self._petition_links(html):
                yield self._petition(link)
    
    def fetch_vote_counts(self, path=URLS["OPEN_PETITIONS_MOST_VOTES_FIRST"]):
        for html in self._each_serp(path):
            for link, count in self._petition_vote_counts(html):
                if count < 100:
                    return
                # XXXX STORE XXXX

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
            self.r.lpush("new-and-untweeted", petition["link"])
            for k, v in petition.items():
                self.r.hset(petition["link"], k, v)

def main():
    logging.basicConfig(level=logging.INFO)
    r = redis.Redis()
    
    petition_store = PetitionStore(r)
    petition_store.get_new_petitions()

if __name__ == "__main__":
    main()
