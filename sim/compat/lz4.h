#ifndef TSE_COMPAT_LZ4_H
#define TSE_COMPAT_LZ4_H

// Used only when the machine has the lz4 runtime (liblz4.so.1) but
// not the -dev header. Verilator's FST writer needs just these two
// calls; their ABI has been stable since lz4 1.7.

#ifdef __cplusplus
extern "C" {
#endif

int LZ4_compressBound(int inputSize);
int LZ4_compress_default(const char* src, char* dst, int srcSize, int dstCapacity);

#ifdef __cplusplus
}
#endif

#endif
