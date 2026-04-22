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
source="travelbook.py travelbook_core.py travelbook_providers.py travelbook_services.py travelbook_widgets.py travelbook.desktop travelbook.svg"
builddir="$srcdir"
options="!check"
sha512sums="
03e2459e7f14f782cba6d22a49affd3a7d5f873681b65c983246b1294676311e410fbe49f362bde040ecad014278bdee400e44393559c89db5f1f443b2cbb82e  travelbook.py
1601c744ddb5e329d0a582d8ee161f36de986bd143823c7249f811f085b37aca8fb063e9fac1b0338df41d194bfe155eeb2a9f2e9b00f42dc8c196f835a4b9b4  travelbook_core.py
cadd1fb629ecd5212ad58597a8bdbb64416f2de09d99f865033c6f563c2bd8edbaaed3ccb887561927f2382f358e3b137d655c60618ac47779ad32438b28eadb  travelbook_providers.py
dbd7bf623396d581004020d67b11acf7bcc6b96a0f402596f16ce1f3e0ab8251df6bd9ef6aff8b2f03373b87346e9d3e4b1e03cdf50628fb417959db56582d22  travelbook_services.py
1f60c9b80ae38643a02a02351d2f53e3de6fd6fcf8592e5ab204399e9ce1719d2bf008c9d46479dd00df6a5ef5876c7cf7699765b81b1b4b7bf26599f5cb154f  travelbook_widgets.py
1b17b2b9414d5be5b81e77e83ca998257a2798f483a73855e0ffe691893b61ca33400d4486e7a790483989af8932f6f90a9f10ab78e7e9c783854acb8d2f7a9b  travelbook.desktop
2b6a93c665fd62f87e639ecc9913fa65a1b7af212f9e7ff900b1411035dbe8b540111db623a684049b5ddd46f05e1909ec5c8a17a4e6085c567fba2842927ca1  travelbook.svg
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
	install -Dm644 "$srcdir"/travelbook.desktop "$pkgdir"/usr/share/applications/travelbook.desktop
	install -Dm644 "$srcdir"/travelbook.svg "$pkgdir"/usr/share/icons/hicolor/scalable/apps/travelbook.svg
}
