# Architettura (versione 2)

Tutto quello che segue è stato **provato nel gioco del giocatore** (EU5 1.3.11,
build release, `-debug_mode`) con una mod-sonda prima di costruirci sopra.

## Perché la versione 1 non funzionava

La versione 1 contava su `debug_log` / `error_log` per far parlare lo script. La
documentazione di quegli effetti è dentro `eu5.exe`, ma nella build release sono
compilati via: il gioco li segnala come *Unknown effect*. Non esiste un effetto
di script che scriva su file.

## Il canale che funziona

```
clic in gioco
  └─ l'effetto della mod alza var:votc_sig_<tipo> sul paese del giocatore
       └─ gui/votc_bridge.gui (widget invisibile, creato da scripted_widgets)
            state trigger_when → ExecuteConsoleCommandsForced(Localize('votc_emit_<tipo>'))
              └─ una lista di comandi "votc_say ..." con i dati del gioco già risolti
                   └─ la console li registra in console_history.txt  ──►  Court Brain
```

- `votc_say` non è un comando: fallisce senza danni, ma la console lo scrive
  comunque nella cronologia. *Provato:* `votc_say ciao prova` è arrivato su disco
  al clic.
- I dati viaggiano dentro le stringhe di localizzazione, che il gioco risolve al
  momento del clic. *Provato:* `votc_say E tag=URB nome=Urbino stab=29`.
- L'ultimo comando della lista (`effect remove_variable = votc_sig_<tipo>`)
  abbassa il segnale. *Provato:* un `run` da interfaccia ha alzato una variabile
  e l'osservatore ha scritto `votc_say D variabile_vista` da solo.

## Il ritorno verso il gioco (senza tasti simulati)

Versione 3. Ogni mail è scritta **dentro** `run/votc_poll.txt`, che il ponte
esegue ogni 3 secondi con un solo comando della console:

    if = { limit = { votc_mail_is_new = { seq = N } } votc_mail_open = { seq = N } ... votc_in_ack = yes }
    else = { votc_bridge_watchdog = yes }

La versione precedente lanciava un secondo comando (`run votc_in.txt`) nello
stesso istante del controllo: la console lo rifiutava («still running
commands») e la posta restava bloccata, con conseguenze, battiti e punto della
memoria mai arrivati. Ora restano due soli comandi a parte, entrambi ritardati
(ricarica dei testi 0,8 s dopo, salvataggio 1,6 s dopo), e ogni segnale del
ponte parte 0,6 s dopo essere stato alzato. Il cane da guardia rilancia un
segnale rimasto alzato per due controlli di fila. La mail con il testo
dell'evento è confermata solo dopo la ricarica (`votc_loc_done`), e la mail che
mostra l'evento parte dopo quella conferma.

## La tendina

`tools/court_brain/courtbrain/drawer.py`: finestra Tk senza bordi, sempre in
primo piano, agganciata al bordo destro dell'area client di EU5. Usa i colori dei
pannelli del gioco e i font presi dalla cartella del gioco (Cormorant Garamond e
Noto Serif, caricati solo per questo processo). Scorre dentro quando arriva un
segnale e si nasconde quando il giocatore lascia il gioco.

È disegnata come una finestra di EU5: intestazione su un `Canvas` a gradiente,
pulsanti a placca (`Plate`), barre di sezione come l'outliner (`Bar`), riquadri
incassati con bordo dorato e una barra di scorrimento sottile (`ThinScroll`).
Gli ornamenti (angoli a giglio, fregio, divisore con rombo) vengono da
`gfx.py`, che decodifica in Python puro tre maschere DXT5 dei file del gioco,
le dora e le tiene come PNG nella cartella di Court Brain (`gfx_v3/`); se
mancano, il pannello si disegna senza.

`ledger.py` è la finestra **Court Brain** che sostituisce la console: stato
(gioco, Player2, campagna, data, letto ogni secondo da `CourtBrain.status()`
senza chiamate di rete) e registro colorato, scritto anche in
`court_brain.log`. `run_court_brain.bat` la avvia con `pyw` (niente console);
un errore all'avvio finisce in `court_brain_crash.log` e in un messaggio.

## Diplomazia

Tre interazioni di paese (`votc_send_envoy`, `votc_request_meeting`,
`votc_state_visit`) chiamano `votc_signal_envoy` con `mode` 1/2/3, che finisce
nella fotografia come `agenda`. La categoria del menu viene dalla chiave
`dip_<azione>_CATEGORY`, che deve essere una delle `CATEGORY_*` del motore:
usiamo `CATEGORY_FRIENDLY_ACTIONS`.

## Conseguenze

**Regola dello script:** EU5 non fa leggere a un effetto una variabile che ha
scritto nella stessa esecuzione. Per questo nessuna azione legge ciò che ha
appena scritto: il controllo del budget è il trigger `votc_can_spend` (legge il
budget com'era all'inizio della mail) seguito dall'effetto `votc_spend`, e il
limite di azioni per scena lo applica Court Brain prima di scrivere la mail.

**Coda delle conseguenze** (`tools/gen_queue.py` → `votc_queue_effects.txt`):
la mail non applica nulla; scrive `votc_queue = { slot id }` (fino a 6 slot) e
l'opzione «Così sia» dell'evento esegue `votc_apply_queue`, così il tooltip del
gioco mostra le conseguenze vere. Gli id vengono da `actions.variants()`: dopo
ogni modifica al catalogo va rigenerato il file. La mail col testo dell'evento
parte da sola e prima dello script (la ricarica della localizzazione deve essere
finita prima che l'evento venga creato, altrimenti compare il testo di riserva).

**Ordini del sovrano** (`votc_order_effects.txt`, `votc_order_*`): quando è il
giocatore a decidere (a voce o col pulsante) non si spende influenza, ma ogni
ordine riuscito applica un costo fisso (stabilità, prestigio, potere di governo).
Se una condizione non regge, l'ordine non avviene, nessun costo è pagato e
l'evento `votc.910` (un giorno dopo, per poter leggere le variabili) spiega il
motivo con un codice tradotto in `votc_orders_l_english.yml`.

- Le conseguenze piccole passano da `votc_act_*` (`votc_action_effects.txt`) e
  vengono spedite alla chiusura, insieme all'evento `votc.100` con l'esito.
- Quelle pesanti passano da `votc_do_*` (`votc_diplomacy_effects.txt`) e partono
  solo con il pulsante di conferma nella tendina. Vengono filtrate due volte: in
  Python (`actions.validate_offer`) e nello script, al momento dell'esecuzione.
- Le grandezze sono le costanti di vanilla
  (`main_menu/common/script_values/default_values.txt`). Il budget si rigenera
  sui mesi di gioco trascorsi.

## Contesto

- **Fotografia**: righe HEAD/STATE/MIGHT/REALM/COURT/ESTATES/RULER/HEIR/PERSON
  più il bersaglio del clic (TARGETCHAR, TARGETCOUNTRY, LOCATION), mandate a ogni
  segnale.
- **Codex**: leggi, privilegi, riforme e stati letti dai file installati.
- **Mondo** (`worldsave.py`, `lore.py`): ogni 20 minuti la mail `votc_mail_save`
  fa eseguire `save votc_world`; il salvataggio in testo viene letto per sezioni
  (paesi, governo e leggi, personaggi, diplomazia, guerre, eventi scattati) e
  riassunto per il regno del giocatore e per quello con cui sta parlando.
- **Quadro dal vivo** (`tools/gen_live.py` → `votc_live_effects.txt`, `live.py`):
  a ogni battito e all'apertura del menu Corte, `votc_prepare_live` riempie 30
  variabili-paese sul giocatore (guerra, alleati, rivali, sudditi, signore,
  grandi potenze, vicini, ciascuno con il suo nemico principale) e il ponte le
  manda come righe `LIVE`. `live.news()` confronta due quadri e produce le
  notizie (guerre, paci, nuovi sovrani, alleanze), che finiscono nella memoria.
  Il file `votc_live_effects.txt` è generato: si modifica `tools/gen_live.py`.
- **Memoria** (`memory.py`): un diario ad albero per campagna
  (`court_brain/campaigns/<id>/journal.jsonl`). Ogni voce (fatto, persona,
  filone, pagina di cronaca) ha id, data e id della voce precedente; ciò che la
  corte ricorda è la catena dalla radice a una voce, la *testa*. Caratteri dei
  personaggi e profilo del regno stanno in `meta.json`, fuori dal diario.
- **Campagne e salvataggi**: due variabili globali, `votc_campaign` e
  `votc_memhead`, vivono dentro la partita e quindi dentro ogni salvataggio. Il
  ponte le manda con ogni fotografia (riga `ID`). Ogni mail aggiorna la testa con
  `votc_set_memhead = { campaign from head }`, che nello script cambia la testa
  solo se partita e testa attuali sono quelle per cui la mail è stata scritta
  (così una mail in viaggio non marchia un salvataggio appena caricato). Court
  Brain riconosce un caricamento quando la partita riporta una testa diversa da
  quella che le ha dato, o una data anteriore all'ultimo ricordo, e si porta su
  quella testa. Una partita senza numero riceve `votc_set_campaign`. Il widget
  del ponte chiede una fotografia appena compare (`_show`), cioè dopo ogni
  caricamento. `savesindex.py` legge l'inizio dei salvataggi in testo (data,
  playthrough, campagna, testa) per `--saves` e per rileggere il salvataggio
  giusto dopo un caricamento; i salvataggi del futuro abbandonato non vengono
  usati come quadro del mondo.
- **Prompt** (`prompts.py`): CRAFT (voci distinte, niente formule), IDENTITY
  (ogni regno è sé stesso), DECREE_TASK (tipo di governo × forza della Corona).
- **Player2**: il modello ragiona prima di rispondere e il ragionamento consuma
  `max_tokens`; se si esaurisce la risposta arriva vuota. Default 3000, e a ogni
  risposta vuota il limite raddoppia (fino a 8000).

## File di riferimento

In `docs/` ci sono le liste estratte dal gioco (effetti, trigger, modificatori,
casus belli, vocabolario di script) usate da `tools/validate_mod.py`.
