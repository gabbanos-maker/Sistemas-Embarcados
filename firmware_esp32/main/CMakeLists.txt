idf_component_register(SRCS "main.c" "wire.c" INCLUDE_DIRS "."
    REQUIRES protocol esp_driver_gpio esp_driver_gptimer esp_driver_uart esp_system)
