# Security Policy

## Reporting a vulnerability

Please do not disclose security vulnerabilities through a public issue. Contact the repository owner privately with reproduction steps, affected versions, and the potential impact.

## Deployment notice

The included Flask server and demonstration credentials are intended for local development. Before deployment:

- change the administrator password and `SECRET_KEY`;
- enable HTTPS and secure cookies;
- add CSRF protection, rate limiting and account lockout;
- restrict media, evidence and database access;
- configure retention, encryption and audit logging;
- run behind a production WSGI server and reverse proxy.

Automated surveillance signals must remain subject to trained human review.
