all:

install:
	@if [ -e  /etc/systemd/system/temper.service ]; then echo ERROR: rm -f /etc/systemd/system/temper.service first; exit 1; fi
	install -m 755 temper.py /usr/local/bin/temper
	install -m 644 temper.service /usr/local/lib/systemd/system/temper.service

uninstall:
	rm -f /usr/local/bin/temper
	rm -f /usr/local/lib/systemd/system/temper.service
