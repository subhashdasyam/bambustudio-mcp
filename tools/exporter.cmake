# Pass this file through CMAKE_PROJECT_INCLUDE during an out-of-tree Studio build.
# It adds a development helper without changing any BambuStudio source or executable.
if(NOT TARGET bambustudio-mcp-exporter)
  add_executable(bambustudio-mcp-exporter "${CMAKE_CURRENT_LIST_DIR}/export_catalog.cpp")
  target_include_directories(bambustudio-mcp-exporter PRIVATE "${CMAKE_SOURCE_DIR}/src" "${CMAKE_BINARY_DIR}/src")
  target_link_libraries(bambustudio-mcp-exporter PRIVATE libslic3r)
endif()
