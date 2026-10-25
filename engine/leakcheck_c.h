/*
 * leakcheck_c.h — FP-Judge memory leak tracker (C submissions)
 *
 * C counterpart of leakcheck.hpp. C has no operator new, so
 * malloc / calloc / realloc / free are intercepted with macros.
 *
 * Tracks the set of pointers this tracker allocated rather than a plain counter.
 * When a submission frees memory allocated inside the library (getline, strdup),
 * a counter would go negative and cancel out a real leak. The set ignores frees
 * of untracked pointers, so that cannot happen.
 *
 * The result is written to the file named by FPJUDGE_LEAK_FILE so the graded
 * output is untouched.
 */
#ifndef FPJUDGE_LEAKCHECK_C_H
#define FPJUDGE_LEAKCHECK_C_H

#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
/* Include <malloc.h> now: if the submission includes it after the macros, its declarations break */
#if defined(__has_include)
#  if __has_include(<malloc.h>)
#    include <malloc.h>
#  endif
#endif

#define FPJ_TOMB ((void *)1)

static void **fpj_tab = NULL;
static size_t fpj_cap = 0;     /* always a power of two */
static size_t fpj_used = 0;    /* live entries + tombstones */
static size_t fpj_live = 0;

static size_t fpj_slot(void *p, size_t cap)
{
    size_t h = (size_t)p;
    h ^= h >> 17;
    h *= (size_t)0x9E3779B1u;
    h ^= h >> 13;
    return h & (cap - 1);
}

static void fpj_put(void **tab, size_t cap, void *p)
{
    size_t i = fpj_slot(p, cap);
    while (tab[i] != NULL && tab[i] != FPJ_TOMB)
        i = (i + 1) & (cap - 1);
    tab[i] = p;
}

static void fpj_add(void *p)
{
    if (p == NULL) return;
    if ((fpj_used + 1) * 2 > fpj_cap) {
        size_t ncap = fpj_cap ? fpj_cap * 2 : 1024, i;
        void **nt = (void **)(calloc)(ncap, sizeof(void *));
        if (nt == NULL) return;
        for (i = 0; i < fpj_cap; i++)
            if (fpj_tab[i] != NULL && fpj_tab[i] != FPJ_TOMB)
                fpj_put(nt, ncap, fpj_tab[i]);
        (free)(fpj_tab);
        fpj_tab = nt;
        fpj_cap = ncap;
        fpj_used = fpj_live;
    }
    fpj_put(fpj_tab, fpj_cap, p);
    fpj_used++;
    fpj_live++;
}

/* Remove p and return 1 if it was tracked, otherwise return 0 */
static int fpj_remove(void *p)
{
    size_t i;
    if (p == NULL || fpj_tab == NULL) return 0;
    for (i = fpj_slot(p, fpj_cap); fpj_tab[i] != NULL; i = (i + 1) & (fpj_cap - 1)) {
        if (fpj_tab[i] == p) {
            fpj_tab[i] = FPJ_TOMB;
            fpj_live--;
            return 1;
        }
    }
    return 0;
}

__attribute__((unused)) static void *fpj_malloc(size_t n)
{
    void *p = (malloc)(n);
    fpj_add(p);
    return p;
}

__attribute__((unused)) static void *fpj_calloc(size_t a, size_t b)
{
    void *p = (calloc)(a, b);
    fpj_add(p);
    return p;
}

__attribute__((unused)) static void *fpj_realloc(void *p, size_t n)
{
    int tracked = fpj_remove(p);
    void *q = (realloc)(p, n);
    if (q != NULL) {
        if (tracked || p == NULL) fpj_add(q);
    } else if (tracked && n != 0) {
        fpj_add(p);                /* a failed realloc leaves the original block allocated */
    }
    return q;
}

__attribute__((unused)) static void fpj_free(void *p)
{
    fpj_remove(p);
    (free)(p);
}

__attribute__((destructor)) static void fpj_report(void)
{
    const char *path = getenv("FPJUDGE_LEAK_FILE");
    FILE *f;
    if (path == NULL) return;
    f = fopen(path, "w");
    if (f == NULL) return;
    fprintf(f, "%lu\n", (unsigned long)fpj_live);
    fclose(f);
}

#define malloc(n)     fpj_malloc(n)
#define calloc(a, b)  fpj_calloc(a, b)
#define realloc(p, n) fpj_realloc(p, n)
#define free(p)       fpj_free(p)

#endif /* FPJUDGE_LEAKCHECK_C_H */
