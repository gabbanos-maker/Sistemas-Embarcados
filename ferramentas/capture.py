#!/usr/bin/env python3
"""Captura bruta e conversão verificável. pyserial só é necessário no modo vivo."""
import argparse
import csv
import json
from pathlib import Path
import secrets
import struct
import time
import zlib

PACKET = struct.Struct('<4sBBBBIIIIQQQQII')
ROW = struct.Struct('<4sBBBBIIIIQIIII')
NONE32 = (1 << 32)-1
NONE = (1 << 64)-1
STATES = {0: '', 1: 'no_prazo', 2: 'atrasada', 3: 'nao_admitida_ocupado',
          4: 'sem_resposta_ao_encerrar'}


class Parser:
    def __init__(self):
        self.buffer = bytearray()
        self.started = False
        self.version = None
        self.errors = 0
        self.skipped_bytes = 0

    def feed(self, data):
        self.buffer.extend(data)
        while len(self.buffer) >= 8:
            at = self.buffer.find(b'SE10')
            if at < 0:
                if self.started:
                    self.skipped_bytes += len(self.buffer)-3
                del self.buffer[:-3]
                break
            if at:
                if self.started:
                    self.skipped_bytes += at
                del self.buffer[:at]
            if len(self.buffer) < 8:
                break
            version, kind = self.buffer[4:6]
            if version not in (1, 2) or kind not in (1, 2, 3, 4):
                if self.started:
                    self.errors += 1
                del self.buffer[0]
                continue
            layout = ROW if version == 2 and kind == 2 else PACKET
            if len(self.buffer) < layout.size:
                break
            frame = bytes(self.buffer[:layout.size])
            fields = layout.unpack(frame)
            if (zlib.crc32(frame[:-4]) != fields[-1] or
                    (self.version is not None and version != self.version)):
                if self.started:
                    self.errors += 1
                del self.buffer[0]
                continue
            if layout is ROW:
                magic, ver, typ, state, phase, seq, run, index, flags, planned, offset, response, delay, crc = fields
                req = NONE if offset == NONE32 else planned+offset
                ack = NONE if response == NONE32 or req == NONE else req+response
                fields = (magic, ver, typ, state, phase, seq, run, index, flags,
                          planned, req, ack, NONE if delay == NONE32 else delay, 0, crc)
            self.started = True
            self.version = version
            del self.buffer[:layout.size]
            yield fields


def decode(raw_path, csv_path, run_id):
    parser = Parser()
    errors = []
    begin = end = integrity = None
    seq = 0
    tag = None
    seen = set()
    counts = [0]*5
    warmup_rows = 0
    max_delay = 0
    header = ['execucao', 'fase', 'indice', 'previsto_us', 'emissao_us', 'ack_us',
              'resposta_us', 'estado', 'atraso_gerador_us', 'falha_gerador']
    with Path(raw_path).open('rb') as raw, Path(csv_path).open('x', newline='') as out:
        writer = csv.writer(out)
        writer.writerow(header)
        while chunk := raw.read(65536):
            for f in parser.feed(chunk):
                _, _, kind, state, phase, number, run, index, flags, a, b, c, d, extra, _ = f
                if number != seq:
                    errors.append(f'sequência esperada {seq}, recebida {number}')
                seq = number+1
                if tag is None:
                    tag = run
                if run != tag:
                    errors.append('identificador de execução mudou')
                if end is not None:
                    errors.append('dados após END')
                if kind == 1:
                    if begin is not None or number != 0:
                        errors.append('BEGIN duplicado/fora de ordem')
                    begin = dict(versao_protocolo=f[1], ofertas=index, periodo_us=a, deadline_us=b,
                                 janela_final_us=c, aquecimento_ofertas=d,
                                 req_gpio=flags & 65535, ack_gpio=flags >> 16,
                                 capacidade_fila=extra)
                elif kind == 2:
                    if begin is None:
                        errors.append('registro sem BEGIN')
                        continue
                    key = (phase, index)
                    if key in seen:
                        errors.append(f'índice duplicado {key}')
                    seen.add(key)
                    limit = begin['ofertas'] if phase == 1 else begin['aquecimento_ofertas']
                    if phase not in (0, 1) or index >= limit:
                        errors.append(f'índice/fase inválido {key}')
                    if state not in STATES:
                        errors.append('estado desconhecido')
                    response = None if b == NONE or c == NONE else c-b
                    if state in (1, 2):
                        if response is None or response < 0:
                            errors.append('resposta sem timestamps válidos')
                        elif (response <= begin['deadline_us']) != (state == 1):
                            errors.append('classificação de deadline divergente')
                    elif state == 3 and (b != NONE or c != NONE):
                        errors.append('busy com emissão ou ACK')
                    elif state == 4 and (b == NONE or c != NONE):
                        errors.append('sem resposta com timestamps incoerentes')
                    if flags or state == 0:
                        errors.append(f'falha do gerador na oferta {key}')
                    if phase == 1:
                        counts[state if state in STATES else 0] += 1
                    else:
                        warmup_rows += 1
                    if d != NONE:
                        max_delay = max(max_delay, d)
                    writer.writerow([run_id, 'coleta' if phase else 'aquecimento', index,
                                     a, '' if b == NONE else b, '' if c == NONE else c,
                                     '' if response is None else response, STATES.get(state, ''),
                                     '' if d == NONE else d, flags])
                elif kind == 3:
                    if integrity is not None:
                        errors.append('INTEGRITY duplicado')
                    integrity = dict(overflow=a, ack_inesperado=b, ofertas_perdidas_gerador=c,
                                     atraso_maximo_gerador_us=d, erros_gpio_timer=extra,
                                     flags=flags)
                    if flags or a or b or c or extra:
                        errors.append('integridade do firmware reprovada')
                elif kind == 4:
                    end = dict(ofertas=index, contagens=[a, b, c, d], flags=flags)
                    if flags:
                        errors.append('END informa falha de integridade')
                else:
                    errors.append(f'tipo desconhecido {kind}')
    if begin is None or end is None or integrity is None:
        errors.append('captura incompleta: faltam BEGIN, INTEGRITY ou END')
    if parser.errors or parser.skipped_bytes or parser.buffer:
        errors.append('CRC, enquadramento ou bytes residuais na captura')
    if begin is not None:
        expected = (10000, 10000) if begin['versao_protocolo'] == 1 else (5000, 3000)
        if (begin['periodo_us'], begin['deadline_us']) != expected:
            errors.append('T/D divergentes do protocolo aprovado')
        if sum(counts) != begin['ofertas'] or warmup_rows != begin['aquecimento_ofertas']:
            errors.append('quantidade de registros divergente do calendário')
    if end is not None:
        if counts[1:] != end['contagens'] or (begin and end['ofertas'] != begin['ofertas']):
            errors.append('contagens locais divergem do END')
    valid = not errors
    n = begin['ofertas'] if begin else 0
    return dict(execucao=run_id, valida=valid, erros=errors, metadados=begin,
                integridade=integrity, run_tag=tag, contagens=dict(zip(STATES.values(), counts)),
                respostas_observadas=counts[1]+counts[2],
                fracao_respostas=(counts[1]+counts[2])/n if valid and n else None,
                taxa_global_falha=sum(counts[2:])/n if valid and n else None,
                crc_erros=parser.errors, bytes_inesperados=parser.skipped_bytes,
                atraso_maximo_gerador_us=max_delay)


def capture(args, destination):
    import serial
    port = serial.Serial(port=None, baudrate=args.baud, timeout=1)
    port.dtr = False
    port.rts = False
    port.port = args.port
    tag = secrets.randbits(32)
    raw_path = destination/'raw.bin'
    parser = Parser()
    with port, raw_path.open('xb') as raw:
        time.sleep(args.start_delay)
        if args.wait_ready:
            input("Porta aberta. Inicie o benchmark na Raspberry, aguarde READY e pressione Enter: ")
        port.write(f'START {tag} {args.offers} {args.warmup}\n'.encode('ascii'))
        deadline = time.monotonic()+(args.offers+args.warmup)*0.005+15
        begin_deadline = time.monotonic()+10
        begin_seen = False
        done = False
        while time.monotonic() < deadline:
            if not begin_seen and time.monotonic() >= begin_deadline:
                raise TimeoutError('ESP32 não enviou BEGIN em 10 s; verifique porta, boot, baudrate e firmware atualizado')
            chunk = port.read(port.in_waiting or 1)
            if not chunk:
                continue
            raw.write(chunk)
            for fields in parser.feed(chunk):
                if fields[2] == 1:
                    begin_seen = True
                if fields[2] == 1 and (fields[1] != 2 or fields[9:11] != (5000, 3000)):
                    raise ValueError('Firmware antigo: recompile e grave T=5000 us, D=3000 us (protocolo v2)')
                if fields[2] == 1 and (fields[6] != tag or fields[7] != args.offers or fields[12] != args.warmup):
                    raise ValueError('BEGIN diverge do START: identificador, ofertas ou aquecimento')
                if fields[6] == tag and fields[2] == 4:
                    done = True
            if done:
                break
        raw.flush()
    return raw_path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--port', help='UART USB do ESP32; requer pyserial')
    source.add_argument('--decode', type=Path, help='raw.bin já preservado')
    p.add_argument('--run-id', required=True)
    p.add_argument('--output', type=Path, required=True, help='pasta nova, nunca sobrescrita')
    p.add_argument('--baud', type=int, default=115200)
    p.add_argument('--wait-ready', action='store_true', help='abre a porta antes do benchmark e aguarda Enter, evitando reset USB durante o atendimento')
    p.add_argument('--start-delay', type=float, default=3.0, help='espera após abrir a porta; permite estabilizar o reset USB do gerador')
    p.add_argument('--offers', type=int, default=120000,
                   help='ofertas da coleta principal: 120000 = 10 min (padrão da aula)')
    p.add_argument('--warmup', type=int, default=6000, help='ofertas fora da coleta: 6000 = 30 s')
    args = p.parse_args()
    if not 1 <= args.offers <= 720000 or not 0 <= args.warmup <= 12000:
        p.error('ofertas: 1..720000; aquecimento: 0..12000')
    args.output.mkdir(parents=True, exist_ok=False)
    try:
        raw = args.decode if args.decode else capture(args, args.output)
        summary = decode(raw, args.output/'ofertas.csv', args.run_id)
    except Exception as e:
        (args.output/'erro.txt').write_text(str(e)+'\n')
        raise
    (args.output/'resumo.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False)+'\n')
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary['valida'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
