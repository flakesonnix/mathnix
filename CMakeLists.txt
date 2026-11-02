cmake_minimum_required(VERSION 3.20)
project(mathnix VERSION 0.1.0 LANGUAGES CXX)

include(GNUInstallDirs)
find_package(PkgConfig REQUIRED)
# Nix's evaluator API is not ABI-stable. Always use the matching Nix binary.
pkg_check_modules(NIX REQUIRED IMPORTED_TARGET nix-expr>=2.34 nix-expr<2.35)

add_library(mathnix MODULE src/plugin.cc)
target_compile_features(mathnix PRIVATE cxx_std_23)
target_compile_options(mathnix PRIVATE -Wall -Wextra -Wconversion)
target_link_libraries(mathnix PRIVATE PkgConfig::NIX)
set_target_properties(mathnix PROPERTIES PREFIX "")
install(TARGETS mathnix LIBRARY DESTINATION "${CMAKE_INSTALL_LIBDIR}/nix/plugins")
install(FILES README.md LICENSE DESTINATION "${CMAKE_INSTALL_DOCDIR}")
install(DIRECTORY examples DESTINATION "${CMAKE_INSTALL_DATADIR}/mathnix")

include(CTest)
if(BUILD_TESTING)
    find_package(Python3 REQUIRED COMPONENTS Interpreter)
    find_program(NIX_INSTANTIATE nix-instantiate REQUIRED)
    add_test(NAME mathnix COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/test_plugin.py"
        --nix "${NIX_INSTANTIATE}" --plugin "$<TARGET_FILE:mathnix>")
endif()
