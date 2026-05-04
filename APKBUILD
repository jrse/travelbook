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
d3667387a9bc680fe912d86e8d954f4e8642dd9717f6a4af009db39453d3915504621c78b8a1acf423b891404dd8c844e6cdaed0a39e087ae89b024e9774ba40  travelbook.py
e5ca4a2d9a211d0ff3ef0b93fca8e348369e10d44516c4c36ab74a92624546ccc993293bcc6572f3c1c4f526daa8f788c1b6b429fc1a2864c30de9a68a747dd6  travelbook_core.py
cadd1fb629ecd5212ad58597a8bdbb64416f2de09d99f865033c6f563c2bd8edbaaed3ccb887561927f2382f358e3b137d655c60618ac47779ad32438b28eadb  travelbook_providers.py
fee7951e00ce5c8fdaa48c8cca61e7203045f26a2291d71376fca10b089c679ab60ba5f90bdd75f51fa24f327d55a951429ca676e3790a6eeae0c1f217ddaa45  travelbook_services.py
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
