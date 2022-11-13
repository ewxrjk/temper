prefix?=/usr/local
bindir?=${prefix}/bin
systemddir?=${prefix}/lib/systemd/system/

all:

install: installdirs
	@if [ -e  ${DESTDIR}/etc/systemd/system/temper.service ]; then echo ERROR: rm -f ${DESTDIR}/etc/systemd/system/temper.service first; exit 1; fi
	install -m 755 temper.py ${DESTDIR}/${bindir}/temper
	sed < temper.service > ${DESTDIR}${systemddir}/temper.service s',/usr/local/bin,${bindir},g'
	chmod 644 ${DESTDIR}${systemddir}/temper.service 

installdirs:
	mkdir -p  ${DESTDIR}${bindir}
	mkdir -p  ${DESTDIR}${systemddir}

uninstall:
	rm -f ${DESTDIR}/${bindir}/temper
	rm -f ${DESTDIR}${systemddir}/temper.service
