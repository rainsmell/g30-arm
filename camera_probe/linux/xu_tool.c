/*
 * xu_tool.c - UVC Extension Unit explorer / controller for the arm camera.
 *
 * Target device: Sonix USB camera  VID 0x0c45  PID 0x636b
 * XU GUID      : 28F03370-6311-4A2E-BA2C-6890EB334016  (bUnitID 3)
 *
 * Build:
 *   sudo apt install libuvc-dev libusb-1.0-0-dev    (or build libuvc from source)
 *   gcc -O2 -o xu_tool xu_tool.c -luvc -lusb-1.0
 *
 * Usage:
 *   ./xu_tool                      # list XU units + every control (LEN/INFO/MIN/MAX/RES/DEF/CUR)
 *   ./xu_tool get  <unit> <sel>    # GET_CUR
 *   ./xu_tool set  <unit> <sel> <hexbytes>   e.g.  ./xu_tool set 3 1 01
 *   ./xu_tool led                  # walk every writable control, pause 2 s each (watch the fill light!)
 *   ./xu_tool diag                 # libuvc device/format diagnostics
 *
 * Notes:
 *   UVC GET_INFO bitmap: bit0 = GET_CUR supported, bit1 = SET_CUR supported,
 *                        bit2/bit3 = currently disabled (auto-update).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <libuvc/libuvc.h>

#define WANT_VID 0x0c45
#define WANT_PID 0x636b

static void hexdump(const unsigned char *b, int n)
{
    for (int i = 0; i < n; i++) printf("%02x", b[i]);
}

static void print_guid(const unsigned char *g)
{
    /* UVC stores the GUID in little-endian (same as an in-memory Windows GUID) */
    printf("%02X%02X%02X%02X-%02X%02X-%02X%02X-%02X%02X-%02X%02X%02X%02X%02X%02X",
           g[3], g[2], g[1], g[0], g[5], g[4], g[7], g[6],
           g[8], g[9], g[10], g[11], g[12], g[13], g[14], g[15]);
}

static uvc_device_handle_t *open_dev(uvc_context_t **ctx_out, uvc_device_t **dev_out)
{
    uvc_context_t *ctx = NULL;
    uvc_device_t *dev = NULL;
    uvc_device_handle_t *devh = NULL;
    uvc_error_t res;

    res = uvc_init(&ctx, NULL);
    if (res < 0) { uvc_perror(res, "uvc_init"); return NULL; }

    res = uvc_find_device(ctx, &dev, WANT_VID, WANT_PID, NULL);
    if (res < 0) { uvc_perror(res, "uvc_find_device (camera not plugged in?)"); uvc_exit(ctx); return NULL; }

    res = uvc_open(dev, &devh);
    if (res < 0) { uvc_perror(res, "uvc_open"); uvc_unref_device(dev); uvc_exit(ctx); return NULL; }

    *ctx_out = ctx;
    *dev_out = dev;
    return devh;
}

static void close_dev(uvc_context_t *ctx, uvc_device_t *dev, uvc_device_handle_t *devh)
{
    uvc_close(devh);
    uvc_unref_device(dev);
    uvc_exit(ctx);
}

/* returns len (>0) or negative */
static int ctrl_len(uvc_device_handle_t *devh, int unit, int sel)
{
    int len = uvc_get_ctrl_len(devh, (uint8_t)unit, (uint8_t)sel);
    return len;
}

static int cmd_info(uvc_device_handle_t *devh)
{
    const uvc_extension_unit_t *xu = uvc_get_extension_units(devh);
    if (!xu) { printf("no extension units reported by libuvc\n"); return 0; }
    for (const uvc_extension_unit_t *u = xu; u; u = u->next) {
        printf("XU unit=%u guid=", u->bUnitID);
        print_guid(u->guidExtensionCode);
        printf(" bmControls=0x%llx\n", (unsigned long long)u->bmControls);

        printf("  %3s %5s %6s  %-24s %-24s %-24s %-24s\n",
               "sel", "len", "info", "MIN", "MAX", "DEF", "CUR");
        for (int sel = 1; sel <= 32; sel++) {
            int len = ctrl_len(devh, u->bUnitID, sel);
            if (len <= 0) continue;
            if (len > 64) len = 64;
            unsigned char info = 0, mn[64], mx[64], rs[64], df[64], cu[64];
            memset(mn, 0, sizeof mn); memset(mx, 0, sizeof mx); memset(rs, 0, sizeof rs);
            memset(df, 0, sizeof df); memset(cu, 0, sizeof cu);
            uvc_get_ctrl(devh, u->bUnitID, sel, &info, 1, UVC_GET_INFO);
            uvc_get_ctrl(devh, u->bUnitID, sel, mn, len, UVC_GET_MIN);
            uvc_get_ctrl(devh, u->bUnitID, sel, mx, len, UVC_GET_MAX);
            uvc_get_ctrl(devh, u->bUnitID, sel, rs, len, UVC_GET_RES);
            uvc_get_ctrl(devh, u->bUnitID, sel, df, len, UVC_GET_DEF);
            uvc_get_ctrl(devh, u->bUnitID, sel, cu, len, UVC_GET_CUR);

            char smn[80] = {0}, smx[80] = {0}, sdf[80] = {0}, scu[80] = {0};
            int n, cap = (len < 16 ? len : 16);
            n = 0; for (int i = 0; i < cap; i++) n += sprintf(smn + n, "%02x", mn[i]);
            n = 0; for (int i = 0; i < cap; i++) n += sprintf(smx + n, "%02x", mx[i]);
            n = 0; for (int i = 0; i < cap; i++) n += sprintf(sdf + n, "%02x", df[i]);
            n = 0; for (int i = 0; i < cap; i++) n += sprintf(scu + n, "%02x", cu[i]);

            printf("  %3d %5d 0x%02x    %-24s %-24s %-24s %-24s %s%s\n",
                   sel, len, info, smn, smx, sdf, scu,
                   (info & 0x01) ? "G" : "-", (info & 0x02) ? "S" : "-");
        }
    }
    return 0;
}

static int cmd_get(uvc_device_handle_t *devh, int unit, int sel)
{
    int len = ctrl_len(devh, unit, sel);
    if (len <= 0) { printf("sel %d: no length (err %d)\n", sel, len); return 1; }
    if (len > 64) len = 64;
    unsigned char buf[64];
    memset(buf, 0, sizeof buf);
    int r = uvc_get_ctrl(devh, (uint8_t)unit, (uint8_t)sel, buf, len, UVC_GET_CUR);
    printf("GET unit=%d sel=%d len=%d ret=%d : ", unit, sel, len, r);
    hexdump(buf, len); printf("\n");
    return 0;
}

static int hex2bin(const char *s, unsigned char *out, int max)
{
    int n = 0;
    while (s[0] && s[1] && n < max) {
        char t[3] = { s[0], s[1], 0 };
        out[n++] = (unsigned char)strtol(t, NULL, 16);
        s += 2;
    }
    return n;
}

static int cmd_set(uvc_device_handle_t *devh, int unit, int sel, const char *hex)
{
    unsigned char buf[64];
    memset(buf, 0, sizeof buf);
    int n = hex2bin(hex, buf, sizeof buf);
    if (n == 0) { printf("bad hex\n"); return 1; }
    int r = uvc_set_ctrl(devh, (uint8_t)unit, (uint8_t)sel, buf, n);
    printf("SET unit=%d sel=%d len=%d ret=%d : ", unit, sel, n, r);
    hexdump(buf, n); printf("\n");
    return 0;
}

static int cmd_led(uvc_device_handle_t *devh)
{
    const uvc_extension_unit_t *xu = uvc_get_extension_units(devh);
    if (!xu) { printf("no XU\n"); return 1; }
    int step = 0;
    for (const uvc_extension_unit_t *u = xu; u; u = u->next) {
        for (int sel = 1; sel <= 32; sel++) {
            int len = ctrl_len(devh, u->bUnitID, sel);
            if (len <= 0 || len > 16) continue;
            unsigned char info = 0;
            uvc_get_ctrl(devh, u->bUnitID, sel, &info, 1, UVC_GET_INFO);
            if (!(info & 0x02)) continue;   /* SET_CUR not supported */
            unsigned char orig[16];
            memset(orig, 0, sizeof orig);
            if (uvc_get_ctrl(devh, u->bUnitID, sel, orig, len, UVC_GET_CUR) < 0) continue;

            /* candidate values */
            unsigned char cand[6][16];
            int nc = 0;
            memset(cand[nc], 0x00, 16); nc++;                 /* all zero   */
            memset(cand[nc], 0xff, 16); nc++;                 /* all ones   */
            memset(cand[nc], 0x00, 16); cand[nc][0] = 0x01; nc++;
            memset(cand[nc], 0x00, 16); cand[nc][len - 1] = 0x01; nc++;
            memset(cand[nc], 0x00, 16); cand[nc][0] = 0x64; nc++;

            for (int i = 0; i < nc; i++) {
                if (uvc_set_ctrl(devh, u->bUnitID, sel, cand[i], len) < 0) continue;
                step++;
                printf("\n>>> STEP %d : unit=%u sel=%d len=%d = ", step, u->bUnitID, sel, len);
                hexdump(cand[i], len);
                printf("   <<<  (watch the fill light, then it restores)\n");
                fflush(stdout);
                sleep(2);
                uvc_set_ctrl(devh, u->bUnitID, sel, orig, len);   /* restore */
                sleep(1);
            }
        }
    }
    printf("\nscanned %d candidate writes. Tell us which STEP lit the light.\n", step);
    return 0;
}

int main(int argc, char **argv)
{
    uvc_context_t *ctx = NULL;
    uvc_device_t *dev = NULL;
    uvc_device_handle_t *devh = open_dev(&ctx, &dev);
    if (!devh) return 1;

    const char *mode = (argc >= 2) ? argv[1] : "info";
    int rc = 0;

    if (!strcmp(mode, "diag")) {
        uvc_print_diag(devh, stdout);
    } else if (!strcmp(mode, "info") || !strcmp(mode, "list")) {
        uvc_print_diag(devh, stdout);
        rc = cmd_info(devh);
    } else if (!strcmp(mode, "get") && argc >= 4) {
        rc = cmd_get(devh, atoi(argv[2]), atoi(argv[3]));
    } else if (!strcmp(mode, "set") && argc >= 5) {
        rc = cmd_set(devh, atoi(argv[2]), atoi(argv[3]), argv[4]);
    } else if (!strcmp(mode, "led")) {
        rc = cmd_led(devh);
    } else {
        printf("usage: %s [info|diag|get <unit> <sel>|set <unit> <sel> <hex>|led]\n", argv[0]);
        rc = 1;
    }

    close_dev(ctx, dev, devh);
    return rc;
}
