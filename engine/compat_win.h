/*
 * compat_win.h — FP-Judge Windows compatibility prelude
 *
 * Submissions were historically written and graded on macOS/Linux and can
 * clash with Windows (mingw-w64) headers. This prelude is injected with
 * -include only after the normal build has failed.
 *
 *   __max / __min : defined as MSVC-compatible macros by mingw-w64 <stdlib.h>;
 *                   a student function with the same name fails to compile.
 *   getline       : POSIX function missing from the mingw-w64 CRT (C submissions).
 *
 * In the leak build this prelude comes before the tracker header, so the
 * realloc calls here are not tracked and freeing a getline buffer does not
 * affect the count.
 */
#ifndef FPJUDGE_COMPAT_WIN_H
#define FPJUDGE_COMPAT_WIN_H

#include <stdlib.h>
#undef __max
#undef __min

#ifndef __cplusplus
#include <stdio.h>
#include <sys/types.h>

__attribute__((unused))
static ssize_t fpj_getdelim(char **lineptr, size_t *n, int delim, FILE *fp)
{
    size_t len = 0;
    int ch;
    if (lineptr == NULL || n == NULL || fp == NULL) return -1;
    if (*lineptr == NULL || *n == 0) {
        *n = 128;
        *lineptr = (char *)realloc(*lineptr, *n);
        if (*lineptr == NULL) return -1;
    }
    while ((ch = fgetc(fp)) != EOF) {
        if (len + 2 > *n) {
            char *p = (char *)realloc(*lineptr, *n * 2);
            if (p == NULL) return -1;
            *lineptr = p;
            *n *= 2;
        }
        (*lineptr)[len++] = (char)ch;
        if (ch == delim) break;
    }
    if (len == 0) return -1;
    (*lineptr)[len] = '\0';
    return (ssize_t)len;
}

#define getdelim(a, b, c, d) fpj_getdelim(a, b, c, d)
#define getline(a, b, c)     fpj_getdelim(a, b, '\n', c)
#endif

#endif /* FPJUDGE_COMPAT_WIN_H */
