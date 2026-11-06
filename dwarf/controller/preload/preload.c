/* Preload exercise fixture (Antithesis spec C02).
 *
 * Antithesis injects its runtime library through /etc/ld.so.preload. This
 * tiny library stands in for it during the controller's adapt step: it has a
 * constructor and a real dependency on libpthread.so.0, so an image whose
 * staged runtime cannot satisfy a preloaded library fails here, before any
 * paid run. It is compiled inside the controller's job directory and is
 * never copied into a node image.
 */
#include <pthread.h>
#include <unistd.h>

static const char marker[] = "dwarf-preload-constructor-ran\n";

__attribute__((constructor)) static void dwarf_preload_constructor(void) {
    pthread_t self = pthread_self();
    (void)self;
    ssize_t written = write(2, marker, sizeof(marker) - 1);
    (void)written;
}
