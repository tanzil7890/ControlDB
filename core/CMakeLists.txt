cmake_minimum_required(VERSION 3.18)
project(controldb_engine VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# ---------------------------------------------------------------------------
# Find dependencies
# ---------------------------------------------------------------------------

find_package(PkgConfig REQUIRED)
pkg_check_modules(ROCKSDB REQUIRED rocksdb)
find_package(OpenSSL REQUIRED)
find_package(ZLIB REQUIRED)

# nlohmann/json (header-only) — fetch via CMake if not installed
find_package(nlohmann_json QUIET)
if(NOT nlohmann_json_FOUND)
    include(FetchContent)
    FetchContent_Declare(
        nlohmann_json
        GIT_REPOSITORY https://github.com/nlohmann/json.git
        GIT_TAG        v3.11.3
    )
    FetchContent_MakeAvailable(nlohmann_json)
endif()

# pybind11 — fetch if not installed
find_package(pybind11 QUIET)
if(NOT pybind11_FOUND)
    include(FetchContent)
    FetchContent_Declare(
        pybind11
        GIT_REPOSITORY https://github.com/pybind/pybind11.git
        GIT_TAG        v2.12.0
    )
    FetchContent_MakeAvailable(pybind11)
endif()

# ---------------------------------------------------------------------------
# Core engine library (shared by bindings + tests)
# ---------------------------------------------------------------------------

add_library(controldb_core STATIC
    src/hash_chain.cpp
    src/wal.cpp
    src/event_store.cpp
    src/raft/log.cpp
    src/raft/node.cpp
)

target_include_directories(controldb_core PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${ROCKSDB_INCLUDE_DIRS}
)

target_link_libraries(controldb_core PUBLIC
    ${ROCKSDB_LIBRARIES}
    OpenSSL::Crypto
    ZLIB::ZLIB
    nlohmann_json::nlohmann_json
    pthread
)

target_compile_options(controldb_core PRIVATE
    -Wall -Wextra -O2 -fPIC
)

# ---------------------------------------------------------------------------
# Python extension module
# ---------------------------------------------------------------------------

pybind11_add_module(_controldb_engine
    bindings/python_bindings.cpp
)

target_link_libraries(_controldb_engine PRIVATE
    controldb_core
)

# Install the .so next to the Python wrapper
install(TARGETS _controldb_engine
    LIBRARY DESTINATION ${CMAKE_CURRENT_SOURCE_DIR}/../services/collector/controldb_collector/storage)

# ---------------------------------------------------------------------------
# C++ unit tests (GoogleTest)
# ---------------------------------------------------------------------------

option(BUILD_TESTS "Build C++ unit tests" ON)
if(BUILD_TESTS)
    enable_testing()

    find_package(GTest QUIET)
    if(NOT GTest_FOUND)
        include(FetchContent)
        FetchContent_Declare(
            googletest
            GIT_REPOSITORY https://github.com/google/googletest.git
            GIT_TAG        v1.14.0
        )
        FetchContent_MakeAvailable(googletest)
    endif()

    add_executable(controldb_tests
        tests/test_hash_chain.cpp
        tests/test_wal.cpp
        tests/test_event_store.cpp
        tests/test_raft.cpp
    )
    target_link_libraries(controldb_tests PRIVATE
        controldb_core
        GTest::gtest_main
    )
    target_include_directories(controldb_tests PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/include
    )
    include(GoogleTest)
    gtest_discover_tests(controldb_tests)
endif()
