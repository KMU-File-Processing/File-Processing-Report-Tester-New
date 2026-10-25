/*
 * leakcheck.hpp — FP-Judge memory leak tracker (C++ submissions)
 *
 * Force-included at compile time with -include; student code is not modified.
 * Replaces the macOS-only `leaks` tool and works with any compiler/OS.
 *
 * How it works:
 *   Global operator new / delete are replaced to count live allocations.
 *   At exit, the remaining count is written to the file named by the
 *   FPJUDGE_LEAK_FILE environment variable. Writing to a file instead of
 *   stdout/stderr keeps the graded output untouched.
 *
 * Because this header is inserted first, the reporter object is constructed
 * before any global object in the submission and therefore destroyed after
 * it. Destructors of a global tree run before the count is taken.
 */
#ifndef FPJUDGE_LEAKCHECK_HPP
#define FPJUDGE_LEAKCHECK_HPP

#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <new>

namespace fpjudge_leak {

inline long long& counter() {
    static long long c = 0;
    return c;
}

struct Reporter {
    ~Reporter() {
        const char* path = std::getenv("FPJUDGE_LEAK_FILE");
        if (path == nullptr) return;
        std::FILE* f = std::fopen(path, "w");
        if (f == nullptr) return;
        std::fprintf(f, "%lld\n", counter());
        std::fclose(f);
    }
};

// Inline variables (C++17) do not compile on GCC < 7 (e.g. MinGW.org GCC 6.3).
// This header is included in a single translation unit, so internal linkage is enough.
static Reporter reporter_instance;

}  // namespace fpjudge_leak

void* operator new(std::size_t n) {
    void* p = std::malloc(n ? n : 1);
    if (p == nullptr) throw std::bad_alloc();
    ++fpjudge_leak::counter();
    return p;
}

void* operator new[](std::size_t n) {
    void* p = std::malloc(n ? n : 1);
    if (p == nullptr) throw std::bad_alloc();
    ++fpjudge_leak::counter();
    return p;
}

void* operator new(std::size_t n, const std::nothrow_t&) noexcept {
    void* p = std::malloc(n ? n : 1);
    if (p != nullptr) ++fpjudge_leak::counter();
    return p;
}

void* operator new[](std::size_t n, const std::nothrow_t&) noexcept {
    void* p = std::malloc(n ? n : 1);
    if (p != nullptr) ++fpjudge_leak::counter();
    return p;
}

void operator delete(void* p) noexcept {
    if (p != nullptr) { --fpjudge_leak::counter(); std::free(p); }
}

void operator delete[](void* p) noexcept {
    if (p != nullptr) { --fpjudge_leak::counter(); std::free(p); }
}

void operator delete(void* p, std::size_t) noexcept {
    if (p != nullptr) { --fpjudge_leak::counter(); std::free(p); }
}

void operator delete[](void* p, std::size_t) noexcept {
    if (p != nullptr) { --fpjudge_leak::counter(); std::free(p); }
}

void operator delete(void* p, const std::nothrow_t&) noexcept {
    if (p != nullptr) { --fpjudge_leak::counter(); std::free(p); }
}

void operator delete[](void* p, const std::nothrow_t&) noexcept {
    if (p != nullptr) { --fpjudge_leak::counter(); std::free(p); }
}

#endif  // FPJUDGE_LEAKCHECK_HPP
