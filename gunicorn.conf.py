import os

bind = '0.0.0.0:' + os.getenv('PORT', '8000')
workers = int(os.getenv('WEB_CONCURRENCY', '2'))
threads = 2
worker_class = 'gthread'
timeout = 60
graceful_timeout = 30
accesslog = '-'
errorlog = '-'
# Do not log query strings containing private receipt tokens.
access_log_format = '%(h)s %(m)s %(U)s %(s)s %(L)s'
