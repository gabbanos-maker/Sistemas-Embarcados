cmake_minimum_required(VERSION 3.16)
# Este projeto tem um alvo próprio; não compartilhe objetos com o outro firmware.
if(DEFINED IDF_TARGET AND NOT IDF_TARGET STREQUAL "esp32")
    message(FATAL_ERROR "Esta pasta é destinada ao ESP32-WROOM; use IDF_TARGET=esp32.")
endif()
set(IDF_TARGET "esp32" CACHE STRING "Alvo deste firmware")
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(se_req_ack)
