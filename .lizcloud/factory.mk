#
# Makefile for building/packaging qgis for lizmap hosting
#

VERSION=$(shell ./metadata_key ../qgis_logger/metadata.txt version)

main:
»   echo "Makefile for packaging infra components: select a task"

PACKAGE=qgis_logger
FILES = ../qgis_logger/*.py ../qgis_logger/metadata.txt ../README.md
PACKAGEDIR=qgis_logger

build/$(PACKAGEDIR):
	@rm -rf build/$(PACKAGEDIR)
	@mkdir -p build/$(PACKAGEDIR)


.PHONY: package
package: build/$(PACKAGEDIR)
	@echo "Building package $(PACKAGE)"
	@cp -rLp $(FILES) build/$(PACKAGEDIR)/
	$(FACTORY_SCRIPTS)/make-package $(PACKAGE) $(VERSION) $(PACKAGEDIR) ./build
