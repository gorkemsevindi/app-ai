"""local_template writer: deterministic, offline story generator.

THIS IS NOT AN LLM. It assembles a coherent multi-episode arc from hand-written TR/EN beat banks so that
the full pipeline (voice, lip-sync, captions, render, publish, monetisation) can be developed and tested
without API keys. Every output is labelled provider="local_template", is_mock=True. Switch
DRAMA_LLM_PROVIDER=anthropic for real writing.
"""

import random
import re

from ..story import (
    CharacterCard,
    EpisodeScript,
    Line,
    Location,
    Look,
    Scene,
    StoryBible,
    VoiceHint,
    WizardInput,
)

NAMES = {
    "tr": {"female": ["Elif", "Defne", "Zeynep", "Asya", "Nehir", "Leyla", "Mira"],
           "male": ["Kerem", "Emir", "Bora", "Can", "Deniz", "Selim", "Arda"]},
    "en": {"female": ["Ava", "Mia", "Nora", "Lena", "Iris", "Clara", "June"],
           "male": ["Leo", "Ethan", "Noah", "Adrian", "Owen", "Julian", "Theo"]},
}

STAKES = {
    "tr": {
        "drama": ("eski mektup", "vasiyet sahteydi"),
        "romance": ("yüzük", "nişan bir anlaşmaydı"),
        "thriller": ("hafıza kartı", "kaza aslında planlanmıştı"),
        "revenge": ("fotoğraf", "yangını çıkaran kişi aramızda"),
        "comedy": ("yanlış düğün davetiyesi", "damat aslında garsondu"),
        "mystery": ("paslı anahtar", "kayıp kardeş hâlâ yaşıyor"),
    },
    "en": {
        "drama": ("an old letter", "the will was forged"),
        "romance": ("a ring", "the engagement was a deal"),
        "thriller": ("a memory card", "the accident was planned"),
        "revenge": ("a photograph", "the person who started the fire is one of us"),
        "comedy": ("the wrong wedding invitation", "the groom is actually the waiter"),
        "mystery": ("a rusty key", "the missing sister is still alive"),
    },
}

LOCATIONS = {
    "tr": [("konak", "Eski konak salonu", "room", "warm"), ("ofis", "Gece ofis katı", "office", "cool"),
           ("sokak", "Yağmurlu sokak", "rain", "night"), ("kafe", "Boğaz kenarı kafe", "cafe", "daylight")],
    "en": [("mansion", "Old mansion hall", "room", "warm"), ("office", "Office floor at night", "office", "cool"),
           ("street", "Rainy street", "rain", "night"), ("cafe", "Seaside café", "cafe", "daylight")],
}

TITLES = {
    "tr": {"drama": "Sessiz Miras", "romance": "Yalan Yüzük", "thriller": "Son Kayıt", "revenge": "Küllerin Altında",
           "comedy": "Yanlış Düğün", "mystery": "Paslı Anahtar"},
    "en": {"drama": "Silent Inheritance", "romance": "The Borrowed Ring", "thriller": "Last Recording",
           "revenge": "Under the Ashes", "comedy": "The Wrong Wedding", "mystery": "The Rusty Key"},
}

# Beat banks: one per episode position, 3 scenes each. Lines: (speaker slot, emotion, text).
# Slots: A protagonist, B antagonist/rival, C ally. {object} {secret} are genre stakes.
BEATS = {
    "tr": [
        ("Keşif", [
            [("A", "surprise", "Bu {object} burada ne arıyor? Yıllardır kimse bu odaya girmedi."),
             ("C", "fear", "Ona dokunma. Lütfen, bunu görmemiş gibi yapalım."),
             ("A", "neutral", "Hayır. Üzerinde benim adım yazıyor."),
             ("C", "sad", "O zaman her şeyi öğrenmeye hazır olmalısın.")],
            [("B", "angry", "Elindeki şeyi hemen bana ver."),
             ("A", "angry", "Neden bu kadar korktun? Neyi saklıyorsun?"),
             ("B", "neutral", "Bazı kapılar kapalı kalmalı. Senin iyiliğin için."),
             ("A", "sad", "Benim iyiliğimi düşünseydin, bana yalan söylemezdin.")],
            [("C", "tender", "Sana bir şey söylemem gerek. Uzun zamandır biliyordum."),
             ("A", "surprise", "Neyi biliyordun?"),
             ("C", "fear", "Gerçeği. {secret}.")],
        ]),
        ("Yüzleşme", [
            [("A", "angry", "Bunu bana nasıl saklarsın? Hepiniz biliyordunuz!"),
             ("C", "sad", "Seni korumaya çalıştım. Yemin ederim."),
             ("A", "sad", "Korumak mı? Bu korumak değil, ihanet.")],
            [("B", "happy", "Sonunda öğrendin demek. Geç kaldın."),
             ("A", "angry", "Bunun hesabını vereceksin."),
             ("B", "neutral", "Kanıtın yok. Sadece eski bir hikâye."),
             ("A", "neutral", "Kanıt cebimde duruyor.")],
            [("C", "fear", "Dikkat et. Bu adamı tanımıyorsun."),
             ("A", "neutral", "Artık tanıyorum. Ve korkmuyorum."),
             ("B", "angry", "O zaman yarın sabah her şeyini kaybedeceksin.")],
        ]),
        ("İhanet", [
            [("A", "tender", "Bana yardım edeceğini söylemiştin."),
             ("C", "sad", "Ediyorum. Ama bedeli çok ağır olacak."),
             ("A", "neutral", "Ödemeye hazırım.")],
            [("B", "surprise", "Buraya gelmeye nasıl cesaret ettin?"),
             ("A", "angry", "Belgeleri gördüm. Her imza senin elinden çıkmış."),
             ("B", "fear", "Kimseye söyleme. Sana istediğin her şeyi veririm."),
             ("A", "neutral", "İstediğim tek şey gerçek.")],
            [("C", "neutral", "Bir şey daha var. Belgeleri ona ben verdim."),
             ("A", "surprise", "Sen mi? Neden?"),
             ("C", "sad", "Çünkü bu hikâyede masum kimse yok. Ben bile.")],
        ]),
        ("Ortaklık", [
            [("A", "neutral", "Tek başıma kazanamam. Sana ihtiyacım var."),
             ("C", "happy", "Bunu duymayı bekliyordum."),
             ("A", "tender", "Bu sefer birbirimize yalan yok.")],
            [("B", "angry", "İkiniz birlikte mi? Gülünç."),
             ("C", "neutral", "Gülmeye devam et. Planını biliyoruz."),
             ("B", "fear", "Bunu kimden öğrendiniz?")],
            [("A", "happy", "İlk kez onu korkmuş gördüm."),
             ("C", "fear", "Korkmuş bir adam daha tehlikelidir."),
             ("A", "surprise", "Kapıda biri var.")],
        ]),
    ],
    "en": [
        ("Discovery", [
            [("A", "surprise", "What is {object} doing here? Nobody has opened this room in years."),
             ("C", "fear", "Don't touch it. Please, let's pretend we never saw it."),
             ("A", "neutral", "No. My name is written on it."),
             ("C", "sad", "Then you'd better be ready for the truth.")],
            [("B", "angry", "Give me what's in your hand. Now."),
             ("A", "angry", "Why are you so scared? What are you hiding?"),
             ("B", "neutral", "Some doors should stay closed. For your own good."),
             ("A", "sad", "If you cared about my good, you wouldn't have lied to me.")],
            [("C", "tender", "I need to tell you something. I've known for a long time."),
             ("A", "surprise", "Known what?"),
             ("C", "fear", "The truth. {secret}.")],
        ]),
        ("Confrontation", [
            [("A", "angry", "How could you keep this from me? All of you knew!"),
             ("C", "sad", "I was trying to protect you. I swear."),
             ("A", "sad", "Protect me? That's not protection. That's betrayal.")],
            [("B", "happy", "So you finally found out. You're too late."),
             ("A", "angry", "You are going to answer for this."),
             ("B", "neutral", "You have no proof. Just an old story."),
             ("A", "neutral", "The proof is in my pocket.")],
            [("C", "fear", "Be careful. You don't know this man."),
             ("A", "neutral", "I know him now. And I'm not afraid."),
             ("B", "angry", "Then tomorrow morning you lose everything.")],
        ]),
        ("Betrayal", [
            [("A", "tender", "You said you would help me."),
             ("C", "sad", "I am. But the price will be heavy."),
             ("A", "neutral", "I'm ready to pay it.")],
            [("B", "surprise", "How dare you come here?"),
             ("A", "angry", "I saw the documents. Every signature is yours."),
             ("B", "fear", "Don't tell anyone. I'll give you anything you want."),
             ("A", "neutral", "The only thing I want is the truth.")],
            [("C", "neutral", "There's one more thing. I gave him the documents."),
             ("A", "surprise", "You did? Why?"),
             ("C", "sad", "Because nobody in this story is innocent. Not even me.")],
        ]),
        ("Alliance", [
            [("A", "neutral", "I can't win alone. I need you."),
             ("C", "happy", "I was waiting to hear that."),
             ("A", "tender", "No more lies between us this time.")],
            [("B", "angry", "The two of you, together? Ridiculous."),
             ("C", "neutral", "Keep laughing. We know your plan."),
             ("B", "fear", "Who told you that?")],
            [("A", "happy", "That's the first time I've seen him afraid."),
             ("C", "fear", "A frightened man is a more dangerous man."),
             ("A", "surprise", "Someone is at the door.")],
        ]),
    ],
}

# Self-contained exchanges used to top up scenes to the target speaking time. Inserted before each
# scene's final beat so cliffhangers stay last. "X" opens, "Y" is the other character in the scene.
EXTRA = {
    "tr": [
        [("X", "neutral", "Bana bak. Gözlerimin içine bak ve doğruyu söyle."),
         ("Y", "sad", "Doğru, ikimizi de yakacak kadar ağır.")],
        [("X", "angry", "Yeter artık! Bu evde herkes bir şey saklıyor."),
         ("Y", "fear", "Sesini alçalt. Duvarların bile kulağı var.")],
        [("X", "neutral", "Yıllarca sustum. Artık susmayacağım."),
         ("Y", "tender", "Seni kaybetmekten korktuğum için sustum."),
         ("X", "surprise", "Beni mi? Ben zaten çoktan kaybolmuştum.")],
        [("X", "sad", "Güven, bu ailede en pahalı şey."),
         ("Y", "angry", "Peki ya ben? Benim de bir bedel ödediğimi hiç düşündün mü?")],
    ],
    "en": [
        [("X", "neutral", "Look at me. Look me in the eyes and tell me the truth."),
         ("Y", "sad", "The truth is heavy enough to burn us both.")],
        [("X", "angry", "Enough! Everyone in this house is hiding something."),
         ("Y", "fear", "Keep your voice down. Even the walls are listening.")],
        [("X", "neutral", "I stayed quiet for years. Not anymore."),
         ("Y", "tender", "I stayed quiet because I was scared of losing you."),
         ("X", "surprise", "Losing me? I was lost a long time ago.")],
        [("X", "sad", "Trust is the most expensive thing in this family."),
         ("Y", "angry", "And what about me? Did you ever think I paid a price too?")],
    ],
}

MOODS = ["mysterious", "tense", "dramatic"]
CAMERAS = ["push_in", "handheld", "pull_out", "pan_left", "static", "pan_right"]

SKIN = ["#f1c7a5", "#e0ac83", "#c68863", "#a8704f", "#8a5a3c", "#5e3b28"]
HAIR = ["#1b1410", "#3b2416", "#6b4423", "#a0663a", "#d8b46a", "#9a9a9a", "#7a1f1f"]
OUTFIT = ["#2c3e50", "#7d2e3b", "#1f4e5f", "#3d3d3d", "#5b3a6e", "#a35c2a", "#2f5d3a"]


def _look(rng: random.Random, gender: str, role: str) -> Look:
    styles = ["long", "bun", "curly", "bob"] if gender == "female" else ["short", "curly", "short", "short", "bald"]
    return Look(skin=rng.choice(SKIN), hair=rng.choice(HAIR), hair_style=rng.choice(styles),
                eyes=rng.choice(["#3b2a1a", "#2e5e4e", "#3a5a8c", "#5b4636"]),
                outfit=rng.choice(OUTFIT), accent=rng.choice(["#c0392b", "#d4ac0d", "#16a085", "#ecf0f1"]),
                face_width=round(rng.uniform(0.92, 1.08), 2), glasses=rng.random() < 0.25,
                beard=gender == "male" and rng.random() < 0.4,
                age_band="senior" if role == "antagonist" and rng.random() < 0.5 else "adult")


def _top_up(scenes: list[Scene], lang: str, target_words: int, offset: int) -> None:
    words = sum(len(ln.text.split()) for sc in scenes for ln in sc.lines)
    bank = EXTRA[lang]
    for k in range(offset, offset + len(bank)):
        if words >= target_words:
            break
        sc = scenes[k % len(scenes)]
        if len(sc.characters) < 2:
            continue
        before = sc.lines[-2] if len(sc.lines) > 1 else sc.lines[0]
        x = next(c for c in sc.characters if c != before.speaker)  # alternate speakers
        y = before.speaker
        new = [Line(speaker=x if slot == "X" else y, look_at=y if slot == "X" else x, emotion=emo, text=text)
               for slot, emo, text in bank[k % len(bank)]]
        sc.lines[-1:-1] = new
        words += sum(len(ln.text.split()) for ln in new)


def generate(inp: WizardInput) -> StoryBible:
    lang = inp.language
    rng = random.Random(inp.seed if inp.seed is not None else hash((inp.genre, inp.logline, lang)) & 0xFFFF)
    obj, secret = STAKES[lang][inp.genre]
    roles = ["protagonist", "antagonist", "ally", "wildcard"][: inp.character_count]
    genders = ["female", "male", "female", "male"]
    rng.shuffle(genders)
    used: set[str] = set()
    cast: list[CharacterCard] = []
    for slot, role in zip("ABCD", roles, strict=False):
        g = genders[len(cast)]
        name = rng.choice([n for n in NAMES[lang][g] if n not in used])
        used.add(name)
        tr = lang == "tr"
        personality = {
            "protagonist": "inatçı, dürüst, kırılgan ama vazgeçmeyen" if tr else "stubborn, honest, fragile but relentless",
            "antagonist": "kontrolcü, sakin, tehditkâr" if tr else "controlling, calm, menacing",
            "ally": "sadık ama sır saklayan" if tr else "loyal but secretive",
            "wildcard": "öngörülemez, esprili" if tr else "unpredictable, witty",
        }[role]
        cast.append(CharacterCard(
            key=re.sub(r"[^a-z]", "", name.lower().translate(str.maketrans("çğıöşüâ", "cgiosua"))) or f"c{slot}",
            name=name, role=role, personality=personality,
            want=("gerçeği öğrenmek" if tr else "to learn the truth") if role == "protagonist" else
                 ("geçmişi gömülü tutmak" if tr else "to keep the past buried"),
            secret=secret if role != "protagonist" else ("hiçbir şey bilmiyor" if tr else "knows nothing yet"),
            look=_look(rng, g, role),
            voice=VoiceHint(gender=g, pitch=rng.randint(35, 70), rate=rng.randint(172, 192),
                            timbre=rng.choice(["warm", "husky", "bright", "deep"])),
        ))
    slot_key = {s: c.key for s, c in zip("ABCD", cast, strict=False)}
    if "C" not in slot_key:
        slot_key["C"] = slot_key["B"]
    locs = [Location(key=k, name=n, ambience=a, lighting=li) for k, n, a, li in LOCATIONS[lang]]

    episodes: list[EpisodeScript] = []
    bank = BEATS[lang]
    for i in range(inp.episode_count):
        beat_title, scenes_tpl = bank[i % len(bank)]
        scenes: list[Scene] = []
        for j, tpl in enumerate(scenes_tpl):
            lines = []
            for slot, emo, text in tpl:
                spk = slot_key[slot]
                other = slot_key["A"] if slot != "A" else slot_key["B" if j == 1 else "C"]
                lines.append(Line(speaker=spk, emotion=emo, look_at=other,
                                  intensity=round(rng.uniform(0.55, 0.95), 2),
                                  text=text.format(object=obj, secret=secret[0].upper() + secret[1:])))
            present = list(dict.fromkeys(ln.speaker for ln in lines))
            scenes.append(Scene(location=locs[(i + j) % len(locs)].key, mood=MOODS[j % 3],
                                time_of_day="night" if (i + j) % 2 else "day",
                                characters=present, camera=CAMERAS[(i * 3 + j) % len(CAMERAS)], lines=lines))
        _top_up(scenes, lang, target_words=int(inp.episode_duration_s * 1.55), offset=i)
        cycle = f" {i // len(bank) + 1}" if i >= len(bank) else ""
        a = cast[0].name
        episodes.append(EpisodeScript(
            number=i + 1, title=f"{beat_title}{cycle}",
            synopsis=(f"{beat_title}: {a} — “{scenes[0].lines[0].text}”"),
            cliffhanger=scenes[-1].lines[-1].text, scenes=scenes))

    title = inp.title or TITLES[lang][inp.genre]
    return StoryBible(
        title=title, logline=inp.logline or (f"{cast[0].name} bulduğu {obj} ile her şeyi sorgulamaya başlar."
                                             if lang == "tr" else f"{cast[0].name} finds {obj} and starts questioning everything."),
        genre=inp.genre, language=lang, tone="gergin, duygusal, cliffhanger odaklı" if lang == "tr" else "tense, emotional, cliffhanger-driven",
        setting="İstanbul, bugün" if lang == "tr" else "A coastal city, present day",
        season_arc=("Gerçeğin peşindeki kahraman, en yakınlarının ihanetiyle yüzleşir." if lang == "tr"
                    else "Chasing the truth, the hero confronts betrayal by those closest."),
        characters=cast, locations=locs, episodes=episodes)
