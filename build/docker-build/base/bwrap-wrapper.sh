#!/bin/sh
# bwrap wrapper for the PlatOS python-node base image.
#
# bwrap is built against musl in the Alpine builder stage; the PlatOS image is
# glibc-based and has no package manager. Keep the musl loader and libcap in a
# private directory so the image does not have to modify /lib or /usr/lib.
set -eu
DIR=/usr/local/lib/muad-sandbox
exec "$DIR/ld-musl.so.1" --library-path "$DIR" "$DIR/bwrap" "$@"
