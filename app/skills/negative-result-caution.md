---
name: negative-result-caution
triggers: [is there, are there, do we have, does it have, is it used, being used, any, exists, do you support, is that supported, no matches, not found]
---

# A zero-result search is weak evidence

Finding nothing means your search terms didn't match. It does not mean the
thing doesn't exist. Asked "is the colour palette being used", a search for
"palette" returned nothing and the answer given was "no" — while the palette
sat in app/ui/console.py under the name VEKTOR_THEME, used by six modules.

Before answering "no", "there isn't one", or "that isn't configured":

- Try the words the code would actually use, not the words the question
  used. palette → theme, colour/color, style, hex codes. auth → login,
  token, credentials, session. Code rarely names things the way people ask
  about them.
- Try both spellings where they differ: colour/color, cancelled/canceled,
  initialise/initialize.
- Open the module whose name matches the concept. If asked about colours,
  read the file called console.py, theme.py, colors.py or styles.py before
  concluding there is no colour handling. A directory listing plus one
  well-chosen read beats three keyword searches.
- Search for a value, not just a name — a hex code, a default, a magic
  string — when you know roughly what the thing would look like.

If you still find nothing, report what you actually did rather than
asserting absence: "I searched for X and Y and found no matches" is honest.
"There is no X" claims knowledge you do not have, and is the kind of
confident wrong answer that sends someone off to build something that
already exists.

The same caution applies to your own capabilities and to the state of the
project: absence of evidence in one search is not evidence of absence.
