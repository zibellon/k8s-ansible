/**
 * Функция генерирует пароль точно по тем же правилам, что и в оригинальном HTML-генераторе.
 *
 * @param {object} options
 * @param {number} [options.length=16]         – желаемая длина итогового пароля
 * @param {boolean} [options.includeLowercase=true] – включать буквы нижнего регистра (a–z)
 * @param {boolean} [options.includeUppercase=true] – включать буквы верхнего регистра (A–Z)
 * @param {boolean} [options.includeNumbers=true]   – включать цифры (0–9)
 * @param {boolean} [options.includeSymbols=false]  – включать «специальные» символы из options.symbols
 * @param {boolean} [options.noSimilar=true]        – исключать похожие символы (i, l, 1, L, 0, O и т.п.)
 * @param {boolean} [options.beginWithLetter=false] – если true, гарантировать, что пароль начинается с буквы
 * @param {boolean} [options.noSequential=false]    – если true, запрещать последовательные пары символов (например, «ab» или «45»)
 * @param {boolean} [options.allUnique=false]       – если true, все символы пароля должны быть разными
 * @param {string}  [options.symbols="!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"] – пул специальных символов (если includeSymbols=true)
 * @returns {string} Сгенерированный пароль
 */
function generatePassword(options = {}) {
  const {
    length = 16,
    includeLowercase = true,
    includeUppercase = true,
    includeNumbers = true,
    includeSymbols = false,
    noSimilar = true,
    beginWithLetter = false,
    noSequential = false,
    allUnique = false,
    symbols = `-_.+!*-_.+!*-_.+!*`,
  } = options;

  // 1) Формируем четыре «категории» символов:
  let lowercase = "abcdefghjkmnpqrstuvwxyz"; // без i, l, o по умолчанию
  let uppercase = "ABCDEFGHJKLMNPQRSTUVWXYZ"; // без I, O
  let numbers = "23456789"; // без 0, 1
  let specials = symbols; // полный пул «специальных» (можно передать свой)

  // Если noSimilar = false, тогда «вернём» все похожие внутрь пулов:
  if (!noSimilar) {
    lowercase += "ilo";
    uppercase += "IO";
    numbers += "01";
    // (если спецсимволы тоже содержат похожие, их можно отдельной логикой)
  }

  // Проверим, чтобы хотя бы одна категория была включена:
  if (!includeLowercase && !includeUppercase && !includeNumbers && !includeSymbols) {
    throw new Error("Нужно выбрать хотя бы одну категорию символов.");
  }

  // 2) Собираем единый пул для «основной» генерации:
  let fullPool = "";
  if (includeLowercase) fullPool += lowercase;
  if (includeUppercase) fullPool += uppercase;
  if (includeNumbers) fullPool += numbers;
  if (includeSymbols) fullPool += specials;

  // Если allUnique=true, пул не должен быть короче length
  if (allUnique && fullPool.length < length) {
    throw new Error("Недостаточно уникальных символов для allUnique=true.");
  }

  const crypto = require("crypto");

  // 3) Инициализируем массив нужной длины и заполняем случайными символами из полного пула:
  //    (пока без гарантий про «по одному из каждой категории»)
  const result = new Array(length);
  for (let i = 0; i < length; i++) {
    // Если allUnique — будем «почти случайно» выбирать и удалять из пула:
    if (allUnique) {
      // Выбираем случайный индекс в текущем fullPool
      const idx = crypto.randomInt(0, fullPool.length);
      result[i] = fullPool[idx];
      // Удаляем этот символ из пула, чтобы он не повторился:
      fullPool = fullPool.slice(0, idx) + fullPool.slice(idx + 1);
    } else {
      // Обычный режим — можем повторяться
      const idx = crypto.randomInt(0, fullPool.length);
      result[i] = fullPool[idx];
    }
  }

  // 4) Теперь «гарантируем», что хотя бы по одному символу из каждой включённой категории
  //    окажется в пароле:
  //    Мы заранее подготовим массив индексов [0, 1, 2, …, length-1],
  //    перемешаем его, и для первых N категорий возьмём первые N индексов:
  const indexes = Array.from({ length }, (_, i) => i);
  for (let i = indexes.length - 1; i > 0; i--) {
    // Fisher–Yates shuffle
    const j = crypto.randomInt(0, i + 1);
    [indexes[i], indexes[j]] = [indexes[j], indexes[i]];
  }
  let cursor = 0; // указывает на следующую «свободную» позицию в перемешанных индексах

  // Если нужно, чтобы пароль начинался с буквы:
  if (beginWithLetter && length > 0) {
    const firstIdx = 0;
    let charForStart = "";

    // Выбираем случайную букву (из той категории, которая включена)
    if (includeLowercase && includeUppercase) {
      // либо верхняя, либо нижняя:
      const lr = crypto.randomInt(0, 2) === 0 ? lowercase : uppercase;
      charForStart = lr[crypto.randomInt(0, lr.length)];
    } else if (includeLowercase) {
      charForStart = lowercase[crypto.randomInt(0, lowercase.length)];
    } else if (includeUppercase) {
      charForStart = uppercase[crypto.randomInt(0, uppercase.length)];
    } else {
      // если буквы отключены, но мы попросили BeginWithLetter — просто пропустим
      charForStart = result[firstIdx];
    }

    result[firstIdx] = charForStart;

    // Уберём 0 из массива свободных индексов, чтобы не перезаписать его ниже
    const posInIndexes = indexes.indexOf(firstIdx);
    if (posInIndexes !== -1) indexes.splice(posInIndexes, 1);
  }

  // Вспомогательная функция, чтобы «вставить» один символ из категории в указанную позицию:
  function ensureFromPool(categoryStr, positionIdx) {
    const r = crypto.randomInt(0, categoryStr.length);
    result[positionIdx] = categoryStr[r];
  }

  // По каждой включённой категории — вставляем 1 символ в случайную «свободную» позицию:
  if (includeLowercase) {
    const pos = indexes[cursor++];
    ensureFromPool(lowercase, pos);
  }
  if (includeUppercase) {
    const pos = indexes[cursor++];
    ensureFromPool(uppercase, pos);
  }
  if (includeNumbers) {
    const pos = indexes[cursor++];
    ensureFromPool(numbers, pos);
  }
  if (includeSymbols) {
    const pos = indexes[cursor++];
    ensureFromPool(specials, pos);
  }

  // 5) Если включён «запрет последовательных символов» (noSequential),
  //    проверяем весь пароль на наличие пар, где код символа i и i+1 отличаются ровно на 1.
  //    Если найдена такая пара, мы можем «заменить» i+1-ый символ на случайный другой,
  //    и свернуть проверку заново, пока не пройдём всю строку без нарушений.
  if (noSequential) {
    let madeChange = true;
    while (madeChange) {
      madeChange = false;
      for (let i = 0; i + 1 < result.length; i++) {
        const c1 = result[i].charCodeAt(0);
        const c2 = result[i + 1].charCodeAt(0);
        if (Math.abs(c2 - c1) === 1) {
          // нашли последовательную пару — переписываем символ i+1:
          // выбираем случайно любой из полного пула (который в этот момент может быть непустой)
          // или пересоздаём из соответствующей категории, если надо жёстко.
          let newChar;
          let poolForReplacement = fullPool;
          if (allUnique) {
            // если allUnique, fullPool уже «укорочен» — используем его
            if (poolForReplacement.length === 0) {
              // больше не из чего взять — просто сдвигаем на +2, чтобы сломать последовательность
              newChar = String.fromCharCode(c2 + 2);
            } else {
              const idx = crypto.randomInt(0, poolForReplacement.length);
              newChar = poolForReplacement[idx];
              fullPool = poolForReplacement.slice(0, idx) + poolForReplacement.slice(idx + 1);
            }
          } else {
            // в не-allUnique режиме заменяем на любой допустимый
            const idx = crypto.randomInt(0, fullPool.length);
            newChar = fullPool[idx];
          }

          result[i + 1] = newChar;
          madeChange = true;
        }
      }
    }
  }

  // 6) Собираем итоговую строку и возвращаем:
  return result.join("");
}

// ========== Пример использования ==========

// Просто выведем 5 паролей, каждый длиной 20 символов, со всеми флажками:
for (let i = 0; i < 20; i++) {
  console.log(
    generatePassword({
      length: 40,
      includeLowercase: true,
      includeUppercase: true,
      includeNumbers: true,
      includeSymbols: true,
      noSimilar: true,
      beginWithLetter: false,
      noSequential: false,
      allUnique: false,
    })
  );
}
