# Firmware de geração e captura — ESP32-WROOM

Esta pasta é exclusiva para o ESP32-WROOM dos alunos; o alvo é verificado durante a compilação.

Ative o ambiente ESP-IDF existente. Dentro desta pasta:

```sh
idf.py --version
idf.py set-target esp32
idf.py build
idf.py -p /dev/ttyACM0 flash
```

Alvo: ESP32 de dois núcleos. REQ: GPIO25 (saída). ACK: GPIO26 (entrada). UART0: GPIO1/GPIO3, 115200 baud, pela interface USB-UART da placa. Ajuste a porta serial ao seu computador.

O projeto usa GPTimer a 1 MHz e GPIO por interrupção. Não inicializa Wi-Fi/Bluetooth. Uma execução por boot; reinicie com EN/RESET somente com o benchmark parado. O coletor envia o comando de início e armazena o fluxo binário. Consulte o tutorial na raiz do pacote para montagem, calibração e os quatro casos.

Compilação verificada com ESP-IDF 5.5.1 e 6.0.2. Isso não substitui o piloto e a validação física das placas utilizadas.

Protocolo vigente: **T=5 ms, D=3 ms**. O aquecimento de 30 s usa 6.000 ofertas, e os 10 minutos de coleta usam 120.000 ofertas. Recompile e grave o firmware atualizado antes de coletar. UART 115200 baud, registros v2 de 48 bytes; o coletor e a análise mantêm suporte offline aos arquivos antigos, sem misturar protocolos.
