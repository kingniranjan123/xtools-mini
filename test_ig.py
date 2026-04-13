import instaloader
import http.cookiejar
L = instaloader.Instaloader()
cj = http.cookiejar.MozillaCookieJar('cookies.txt')
cj.load(ignore_discard=True, ignore_expires=True)
for cookie in cj:
    L.context._session.cookies.set(cookie.name, cookie.value, domain=cookie.domain, path=cookie.path)

try:
    L.context.login('dummy', 'dummy') # Not really login, just populate headers if possible
except: pass

profile = instaloader.Profile.from_username(L.context, '_archana_kamble_12')
for post in profile.get_posts():
    print(post.shortcode)
    break
