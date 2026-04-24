# Maintainer: Your Name <you@example.com>
pkgname=travelbook
pkgver=0.1.0
pkgrel=26
pkgdesc="GPS radar POI viewer using OpenStreetMap for postmarketOS"
url="https://example.com/travelbook"
arch="all"
license="MIT"
depends="python3 py3-requests py3-gobject3 py3-pydbus gtk+3.0 geoclue iio-sensor-proxy font-dejavu"
makedepends=""
source="travelbook.py travelbook_core.py travelbook_providers.py travelbook_services.py travelbook_widgets.py travelbook.desktop travelbook.svg mode-pedestrian.svg mode-drive.svg"
builddir="$srcdir"
options="!check"
sha512sums="
6385ecba494986852549e77f1cc45faf1cac74f463dd9931ce6dcd662e73b4df385af12c172535d41c9ec9f5b2a4551b254cbf857c06271c58593faa23ed9b64  travelbook.py
805ca9ea5f3ec36782f1023e1f559c762ae4172c1c35b2477a192e89434734c90005638a69a90ae1fdf55709e52adb7e9fac172c17a119b327201cc9c4cfb4e7  travelbook_core.py
cadd1fb629ecd5212ad58597a8bdbb64416f2de09d99f865033c6f563c2bd8edbaaed3ccb887561927f2382f358e3b137d655c60618ac47779ad32438b28eadb  travelbook_providers.py
04a20458c3f4ba952e122d07e76bc13466bbbd7aaa726f20f77e6a56705f902385df67eb3780363d287334b42461c399112f4f811d93da1e39d17435f0a607f6  travelbook_services.py
c457934ab390d4735f0bf4a03bf1a7f497965c801e258f6ebf0f31194d2be45da645ad47c3c42e68f09aba35e120776098bea8a12a27154e684945403fbd5ad9  travelbook_widgets.py
1b17b2b9414d5be5b81e77e83ca998257a2798f483a73855e0ffe691893b61ca33400d4486e7a790483989af8932f6f90a9f10ab78e7e9c783854acb8d2f7a9b  travelbook.desktop
2b6a93c665fd62f87e639ecc9913fa65a1b7af212f9e7ff900b1411035dbe8b540111db623a684049b5ddd46f05e1909ec5c8a17a4e6085c567fba2842927ca1  travelbook.svg
33aad012ee93efca0ac44d51ee9c8d43e652657db12aae24ba2a006606940cb625893668587d6610985322f3784de1a218900e94952e655ccc699817d523a77b  mode-pedestrian.svg
b8387411cfc5457bb83e63e19fb27b3e60eecbc8a990b38a6e3c27028a719f7f75adbc275f538becb98e5311f704db8db612f5b1c25a3660846fcf06886d1329  mode-drive.svg
"

build() {
	return 0
}

package() {
	install -Dm755 "$srcdir"/travelbook.py "$pkgdir"/usr/bin/travelbook
	install -Dm644 "$srcdir"/travelbook_core.py "$pkgdir"/usr/bin/travelbook_core.py
	install -Dm644 "$srcdir"/travelbook_providers.py "$pkgdir"/usr/bin/travelbook_providers.py
	install -Dm644 "$srcdir"/travelbook_services.py "$pkgdir"/usr/bin/travelbook_services.py
	install -Dm644 "$srcdir"/travelbook_widgets.py "$pkgdir"/usr/bin/travelbook_widgets.py
	install -Dm644 "$srcdir"/mode-pedestrian.svg "$pkgdir"/usr/bin/mode-pedestrian.svg
	install -Dm644 "$srcdir"/mode-drive.svg "$pkgdir"/usr/bin/mode-drive.svg
	install -Dm644 "$srcdir"/travelbook.desktop "$pkgdir"/usr/share/applications/travelbook.desktop
	install -Dm644 "$srcdir"/travelbook.svg "$pkgdir"/usr/share/icons/hicolor/scalable/apps/travelbook.svg
}
