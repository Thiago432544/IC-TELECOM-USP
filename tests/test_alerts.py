from monitor.alerts import AlertEngine, CamState, Snapshot
from monitor.config import AlertCfg

CFG = AlertCfg(disk_min_gb=60.0, down_after_s=180, degraded_ratio=0.5,
               degraded_after_s=600, flap_count=5, flap_window_s=900,
               realert_s=1800, link_rsrp_min=-110.0, link_rsrq_min=-15.0,
               link_after_s=300)

def cam(age=10.0, rate=60.0, base=60.0, disc=0, rsrp_cam=None, sinr=None):
    return CamState(frames_per_min=rate, last_frame_age_s=age,
                    baseline=base, disconnects_15min=disc,
                    rsrp_cam=rsrp_cam, sinr=sinr)

def snap(cams, disk=200.0, rsrp=None, rsrq=None, erros=0):
    return Snapshot(cameras=cams, disk_free_gb=disk, rsrp=rsrp, rsrq=rsrq,
                    worker_errors=erros)

def kinds(alerts):
    return [(a.kind, a.origin) for a in alerts]

def test_down_fires_once_then_recovers_then_refires():
    e = AlertEngine(CFG)
    assert kinds(e.evaluate(1000, snap({"102": cam(age=200)}))) == [("down", "102")]
    assert e.evaluate(1030, snap({"102": cam(age=230)})) == []          # suprimido
    assert kinds(e.evaluate(1100, snap({"102": cam(age=5)}))) == [("recovered", "102")]
    assert kinds(e.evaluate(1200, snap({"102": cam(age=300)}))) == [("down", "102")]

def test_group_down_replaces_individuals():
    e = AlertEngine(CFG)
    s = snap({"102": cam(age=200), "106": cam(age=210), "105": cam(age=5)})
    out = e.evaluate(1000, s)
    assert kinds(out) == [("group_down", "*")]
    assert "102" in out[0].text and "106" in out[0].text

def test_degraded_needs_persistence_and_baseline():
    e = AlertEngine(CFG)
    low = {"102": cam(age=5, rate=20.0, base=60.0)}
    assert e.evaluate(1000, snap(low)) == []                 # comecou a contar
    assert e.evaluate(1000 + 599, snap(low)) == []           # ainda nao
    assert kinds(e.evaluate(1000 + 601, snap(low))) == [("degraded", "102")]
    e2 = AlertEngine(CFG)
    nobase = {"102": cam(age=5, rate=20.0, base=None)}
    assert e2.evaluate(1000, snap(nobase)) == []
    assert e2.evaluate(2000, snap(nobase)) == []

def test_flapping():
    e = AlertEngine(CFG)
    assert kinds(e.evaluate(1000, snap({"106": cam(disc=5)}))) == [("flapping", "106")]
    assert e.evaluate(1030, snap({"106": cam(disc=6)})) == []  # suprimido 30min

def test_link_needs_persistence():
    e = AlertEngine(CFG)
    s = snap({"102": cam()}, rsrp=-115.0, rsrq=-6.0)
    assert e.evaluate(1000, s) == []
    assert kinds(e.evaluate(1000 + 301, s)) == [("link", "cpe")]

def test_disk_realerts_daily_not_halfhourly():
    e = AlertEngine(CFG)
    s = snap({"102": cam()}, disk=50.0)
    assert kinds(e.evaluate(1000, s)) == [("disk", "pc")]
    assert e.evaluate(1000 + 7200, s) == []                   # 2h: nada
    assert kinds(e.evaluate(1000 + 86401, s)) == [("disk", "pc")]

def test_queda_sustentada_reinsiste_e_diz_a_duracao():
    e = AlertEngine(CFG)
    assert kinds(e.evaluate(1000, snap({"102": cam(age=200)}))) == [("down", "102")]
    assert e.evaluate(1000 + 1799, snap({"102": cam(age=2000)})) == []
    out = e.evaluate(1000 + 1801, snap({"102": cam(age=54000)}))
    assert kinds(out) == [("down", "102")]
    assert "15h" in out[0].text          # e nao "900 min"

def test_flapping_cala_o_par_down_recovered():
    e = AlertEngine(CFG)
    tempestade = {"106": cam(age=200, disc=12)}
    assert kinds(e.evaluate(1000, snap(tempestade))) == [("flapping", "106")]
    assert e.evaluate(1030, snap({"106": cam(age=5, disc=12)})) == []
    assert e.evaluate(1060, snap(tempestade)) == []
    # tempestade passou com a camera no ar: um unico encerramento
    assert kinds(e.evaluate(3000, snap({"106": cam(age=5, disc=0)}))) == [
        ("recovered", "106")]
    assert e.evaluate(3030, snap({"106": cam(age=5, disc=0)})) == []

def test_camera_que_fica_fora_depois_da_tempestade_alerta():
    e = AlertEngine(CFG)
    assert kinds(e.evaluate(1000, snap({"106": cam(age=200, disc=12)}))) == [
        ("flapping", "106")]
    out = e.evaluate(2000, snap({"106": cam(age=1200, disc=0)}))
    assert kinds(out) == [("down", "106")]
    assert "20min" in out[0].text

def test_group_down_sobrevive_ao_flapping():
    # simultaneidade e' diagnostico de enlace; calar por causa da tempestade de
    # uma das cameras esconderia justamente o evento mais grave
    e = AlertEngine(CFG)
    s = snap({"105": cam(age=200, disc=0), "106": cam(age=210, disc=12)})
    out = e.evaluate(1000, s)
    assert ("group_down", "*") in kinds(out)
    assert "105" in out[-1].text and "106" in out[-1].text


def test_o_primeiro_down_notifica_e_os_lembretes_vao_calados():
    """Insistir nao e' acordar: quem ja recebeu o primeiro aviso nao precisa do
    celular vibrando de meia em meia hora a noite inteira."""
    e = AlertEngine(CFG)
    primeiro = e.evaluate(1000, snap({"102": cam(age=200)}))
    assert kinds(primeiro) == [("down", "102")]
    assert primeiro[0].silent is False

    lembrete = e.evaluate(1000 + 1801, snap({"102": cam(age=2000)}))
    assert kinds(lembrete) == [("down", "102")]
    assert lembrete[0].silent is True

    # a volta e' novidade, nao lembrete: essa notifica
    volta = e.evaluate(1000 + 1900, snap({"102": cam(age=5)}))
    assert kinds(volta) == [("recovered", "102")]
    assert volta[0].silent is False


def test_queda_simultanea_notifica_mesmo_sendo_repeticao_de_um_lembrete():
    """Duas cameras juntas e' diagnostico de enlace - nunca vai calado."""
    e = AlertEngine(CFG)
    out = e.evaluate(1000, snap({"102": cam(age=200), "106": cam(age=210)}))
    assert kinds(out) == [("group_down", "*")]
    assert out[0].silent is False


def test_restart_com_cameras_ja_fora_nao_reacusa_a_enodeb():
    """O _down vive so na memoria, entao todo restart redescobria quem ja
    estava fora como queda nova - e com duas ao mesmo tempo disparava o
    group_down, cujo texto diz "provavel enlace/eNodeB".

    Em 30/08/2026 ele disparou com a 102 e a 106 fora, e o diagnostico estava
    errado: a eNodeB mostrou as duas anexadas, com o radio de pe e uplink zero.
    O que estava morto era a Raspberry das duas. O alerta acusava justamente o
    componente saudavel.
    """
    e = AlertEngine(CFG)
    s = snap({"102": cam(age=9 * 86400), "106": cam(age=7 * 86400),
              "105": cam(age=5)})

    e.prime(1000, s)

    assert e.evaluate(1030, s) == []


def test_prime_nao_esconde_queda_nova():
    """Adotar o estado nao pode virar mordaca: quem cair depois do restart
    continua alertando na hora."""
    e = AlertEngine(CFG)
    e.prime(1000, snap({"102": cam(age=9 * 86400), "105": cam(age=5)}))

    out = e.evaluate(1030, snap({"102": cam(age=9 * 86400), "105": cam(age=400)}))

    assert kinds(out) == [("down", "105")]


def test_monitor_cego_avisa_em_vez_de_ficar_quieto():
    """_forever() captura qualquer excecao de worker e grava um evento
    worker_error - que ninguem lia. Um worker estourando todo ciclo deixava o
    monitor no ar, com o /status respondendo, e a medicao parada.

    Num projeto cuja tese e' a falha silenciosa, o monitor falhar em silencio
    era a ironia que faltava fechar.
    """
    e = AlertEngine(CFG)
    out = e.evaluate(1000, snap({"102": cam()}, erros=5))
    assert kinds(out) == [("worker", "monitor")]
    assert "5" in out[0].text


def test_erro_isolado_de_worker_nao_alerta():
    """Uma excecao avulsa fica no banco e nao acorda ninguem. O que importa e'
    o worker que estoura de novo e de novo - esse e' o que cega o monitor."""
    e = AlertEngine(CFG)
    assert e.evaluate(1000, snap({"102": cam()}, erros=1)) == []


def test_alerta_de_worker_insiste_mudo():
    """Mesmo tratamento que a queda sustentada ganhou em 2543838: o primeiro
    avisa, os lembretes ficam no chat sem vibrar."""
    e = AlertEngine(CFG)
    primeiro = e.evaluate(1000, snap({"102": cam()}, erros=5))
    assert primeiro[0].silent is False
    assert e.evaluate(1000 + 1800, snap({"102": cam()}, erros=9)) == []
    repetido = e.evaluate(1000 + 3601, snap({"102": cam()}, erros=9))
    assert kinds(repetido) == [("worker", "monitor")]
    assert repetido[0].silent is True


def test_degraded_insiste_mudo():
    """Uma reducao proposital de frequencia segura a camera abaixo da linha de
    base por horas, porque a base e' historica e demora a acompanhar. O alerta
    repetia de meia em meia hora vibrando o celular - o mesmo erro que a queda
    sustentada corrigiu em 2543838. O primeiro avisa; o lembrete fica no chat.
    """
    e = AlertEngine(CFG)
    low = {"102": cam(age=5, rate=20.0, base=60.0)}
    e.evaluate(1000, snap(low))

    primeiro = e.evaluate(1601, snap(low))
    assert kinds(primeiro) == [("degraded", "102")]
    assert primeiro[0].silent is False

    repetido = e.evaluate(1601 + 1801, snap(low))
    assert kinds(repetido) == [("degraded", "102")]
    assert repetido[0].silent is True


def test_novo_episodio_de_degradada_volta_a_notificar():
    """Fim do episodio limpa a supressao: uma degradacao nova logo depois de
    uma que acabou nao pode ser engolida pela janela de 30 min da anterior."""
    e = AlertEngine(CFG)
    low = {"102": cam(age=5, rate=20.0, base=60.0)}
    ok = {"102": cam(age=5, rate=60.0, base=60.0)}
    e.evaluate(1000, snap(low))
    assert e.evaluate(1601, snap(low))[0].silent is False

    e.evaluate(1700, snap(ok))          # normalizou: encerra o episodio
    e.evaluate(2000, snap(low))         # comeca a contar de novo

    novo = e.evaluate(2601, snap(low))
    assert kinds(novo) == [("degraded", "102")]
    assert novo[0].silent is False


def test_sem_leitura_de_disco_nao_finge_que_ha_espaco():
    """O service passava 1e9 GB quando nao havia amostra. Uma falha de leitura
    - o D: sumindo, por exemplo - virava "disco folgado" e o alerta nunca
    disparava: falha aberta e silenciosa, justamente o que este projeto existe
    para nao cometer. Sem leitura, o engine nao opina."""
    e = AlertEngine(CFG)
    assert e.evaluate(1000, snap({"102": cam()}, disk=None)) == []
    assert kinds(e.evaluate(2000, snap({"102": cam()}, disk=10.0))) == [("disk", "pc")]


def test_radio_fraco_alerta_depois_de_persistir():
    """O radio da camera era so coletado: nenhum limiar olhava para ele. Quem
    tinha alerta era o CPE do lado do SPA, que fica a 400 m da estacao, le
    saturado e nao diz nada sobre as cameras."""
    e = AlertEngine(CFG)
    s = snap({"106": cam(rsrp_cam=-108.0, sinr=18.0)})
    assert e.evaluate(1000, s) == []              # um mergulho nao alerta
    out = e.evaluate(1301, s)
    assert kinds(out) == [("radio", "106")]
    assert "-108" in out[0].text


def test_sinr_baixo_alerta_mesmo_com_rsrp_otimo():
    """Perto e sujo: potencia no teto e ruido alto. O RSRP sozinho leria como
    enlace excelente, e e' o SINR que decide a modulacao, ou seja a vazao."""
    e = AlertEngine(CFG)
    s = snap({"105": cam(rsrp_cam=-50.0, sinr=2.0)})
    e.evaluate(1000, s)
    out = e.evaluate(1301, s)
    assert kinds(out) == [("radio", "105")]
    assert "SINR" in out[0].text


def test_radio_sem_leitura_nao_alerta():
    """Sem chave SSH, com a Pi fora ou com o cron parado nao ha amostra. E
    ausencia de leitura nao e' enlace ruim."""
    e = AlertEngine(CFG)
    s = snap({"106": cam(rsrp_cam=None, sinr=None)})
    e.evaluate(1000, s)
    assert e.evaluate(1301, s) == []


def test_radio_que_melhora_zera_a_contagem():
    e = AlertEngine(CFG)
    ruim = snap({"106": cam(rsrp_cam=-108.0, sinr=18.0)})
    bom = snap({"106": cam(rsrp_cam=-70.0, sinr=20.0)})
    e.evaluate(1000, ruim)
    e.evaluate(1100, bom)
    assert e.evaluate(1301, ruim) == []
