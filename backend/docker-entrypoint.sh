#!/bin/sh

set -eu

aws_config_source=/run/sherlock-aws

if [ -d "$aws_config_source" ]; then
    install -d -m 700 -o app -g app /home/app/.aws
    cp -R "$aws_config_source"/. /home/app/.aws/
    chown -R app:app /home/app/.aws
    chmod -R go-rwx /home/app/.aws
fi

exec setpriv --reuid=app --regid=app --init-groups "$@"
