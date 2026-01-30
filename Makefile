.PHONY: build test lint ci bump submodules man packaging release

build:
	./scripts/build.sh

test:
	./scripts/test.sh

lint:
	./scripts/lint.sh

ci:
	./scripts/local_ci.sh

bump:
	./scripts/bump_version.sh patch

submodules:
	./scripts/update_submodules.sh

man:
	./scripts/generate_man.sh

packaging:
	./scripts/packaging_init.sh

release:
	./scripts/release.sh
