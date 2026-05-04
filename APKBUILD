# Maintainer: Your Name <you@example.com>
pkgname=travelbook
pkgver=0.1.0
pkgrel=27
pkgdesc="GPS radar POI viewer using OpenStreetMap for postmarketOS"
url="https://example.com/travelbook"
arch="all"
license="MIT"
depends="python3 py3-requests py3-gobject3 py3-pydbus gtk+3.0 geoclue iio-sensor-proxy font-dejavu ffmpeg pulseaudio-utils"
makedepends=""
source="travelbook.py travelbook_core.py travelbook_providers.py travelbook_services.py travelbook_widgets.py travelbook.desktop travelbook.svg mode-pedestrian.svg mode-drive.svg"
builddir="$srcdir"
options="!check"
sha512sums="
e40f2a8781d2fbf1c7f3f62b3c61b81845bcdf3b4290abe8024590f29fde5f957f013696287cd668ed65cad5f21fe0808a8f09e1ee98d7f57a9746bf0898f152  travelbook.py
b1953c1700a0542d02a75f923a9b00b733f3fcbddded7764c015a3002abb2c8163c08ff5d3605a22c88526fd46ba0fe222cc9265f6c5d125f7ed8adcdb888945  travelbook_core.py
cadd1fb629ecd5212ad58597a8bdbb64416f2de09d99f865033c6f563c2bd8edbaaed3ccb887561927f2382f358e3b137d655c60618ac47779ad32438b28eadb  travelbook_providers.py
7cb1edec2fc7ef447c80f40d0da70dbf3490e4ff176f83e0f221c17a88a2b42346f679926512ed1252847c4e647ef229a068ebc2f43c2a4352ed0c2038cd6c97  travelbook_services.py
ab69b56becaa0c7533129aaf8c18de6e8e763ae25c871b4a1ad0e776ab8cef4da95f10e8e7f7996809749d59a8c232967d1798f26b4510c96e21a96004a9b6ce  travelbook_widgets.py
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
