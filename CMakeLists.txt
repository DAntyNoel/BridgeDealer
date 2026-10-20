cmake_minimum_required(VERSION 3.16)
project(BridgeDealerDDS VERSION 3.1.0 LANGUAGES CXX)

# BridgeDealer wrapper only: the pinned upstream DDS sources are unchanged.
if(NOT APPLE AND NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
    message(FATAL_ERROR "Supported build hosts: macOS, Linux, or Windows via WSL2")
endif()
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
find_package(Threads REQUIRED)
set(DDS_SRC "${CMAKE_CURRENT_SOURCE_DIR}/vendor/dds/library/src")
# This list corresponds to the DDS 3.1.0 library and C-ABI shim Bazel targets.
set(DDS_SOURCES
    ab_search.cpp
    ab_stats.cpp
    api/dds_c_api.cpp
    calc_dd_table.cpp
    calc_par.cpp
    calc_tables.cpp
    dds.cpp
    dds_api.cpp
    dealer_par.cpp
    dump.cpp
    heuristic_sorting/heuristic_sorting.cpp
    init.cpp
    later_tricks.cpp
    lookup_tables/lookup_tables.cpp
    moves/moves.cpp
    par.cpp
    pbn.cpp
    play_analyser.cpp
    quick_tricks.cpp
    solve_board.cpp
    solver_context/solver_context.cpp
    solver_context_adapter.cpp
    solver_if.cpp
    system/deal_fanout.cpp
    system/file.cpp
    system/memory.cpp
    system/parallel_boards.cpp
    system/scheduler.cpp
    system/system.cpp
    system/thread_data.cpp
    system/thread_mgr.cpp
    system/time_stat.cpp
    system/time_stat_list.cpp
    system/timer.cpp
    system/timer_group.cpp
    system/timer_list.cpp
    trans_table/trans_table_l.cpp
    trans_table/trans_table_s.cpp
    utility/constants.cpp
)
list(TRANSFORM DDS_SOURCES PREPEND "${DDS_SRC}/")
add_library(dds SHARED ${DDS_SOURCES})
target_include_directories(dds PRIVATE "${DDS_SRC}" "${DDS_SRC}/system" "${DDS_SRC}/solver_context")
target_link_libraries(dds PRIVATE Threads::Threads)
set_target_properties(dds PROPERTIES
    POSITION_INDEPENDENT_CODE ON
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/lib"
)
