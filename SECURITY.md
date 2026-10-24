# Security

Minutara handles recordings of private conversations, so security reports are
taken seriously.

## Reporting a vulnerability

Please **don't open a public issue**. Report it privately through GitHub:
the repository's **Security** tab → **Report a vulnerability**. Include what
you found, how to reproduce it, and what an attacker could do with it.

You'll get a reply within a week. Once a fix is released, you'll be credited
in the release notes unless you'd rather not be.

## What's in scope

- The local server (`minutara/server.py`): it listens on your network for the
  phone remote and is protected by a token. Anything that gets around the
  token, or reads data it shouldn't, is in scope.
- Data leaving the computer when the relevant setting is off.
- API keys or tokens exposed to the browser, the log or the backup.
- The Windows build and release process.

## Supported versions

Only the latest release gets fixes. Update to it before reporting.
