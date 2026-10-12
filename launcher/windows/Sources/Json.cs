// SPDX-License-Identifier: MIT
// Copyright (C) 2026 Nicholas Vlamis

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace GammonView.Installer
{
    /// <summary>
    /// The smallest JSON reader that answers the questions this program asks.
    ///
    /// Written out rather than borrowed. .NET Framework 4.8 -- the runtime
    /// every Windows 10 and 11 machine already has, which is why the installer
    /// targets it -- has no `System.Text.Json`, and its own JSON readers are
    /// either typed contracts or `System.Web`, neither of which can say the one
    /// thing `Installer.LinkedVerdict` depends on: whether a key is *present
    /// and null* or *absent*. A package dependency would answer it, and would
    /// be one more thing to resolve the next time this frozen artefact has to
    /// be rebuilt, years from now.
    ///
    /// Objects become `Dictionary&lt;string, object?&gt;`, arrays
    /// `List&lt;object?&gt;`, numbers `double`, and `null` is C# null -- so
    /// "present and null" is `ContainsKey` and a null value.
    /// </summary>
    public static class Json
    {
        /// <summary>The parsed value, or throws <see cref="FormatException"/>.</summary>
        public static object? Parse(string text)
        {
            var reader = new Reader(text);
            reader.SkipSpace();
            var value = reader.Value();
            reader.SkipSpace();
            if (!reader.AtEnd) throw new FormatException("trailing characters");
            return value;
        }

        /// <summary>A JSON object, or null for anything else -- including garbage.</summary>
        public static Dictionary<string, object?>? Object(string? text)
        {
            if (string.IsNullOrEmpty(text)) return null;
            try { return Parse(text!) as Dictionary<string, object?>; }
            catch (FormatException) { return null; }
        }

        public static string? String(Dictionary<string, object?> o, string key) =>
            o.TryGetValue(key, out var v) ? v as string : null;

        public static bool? Bool(Dictionary<string, object?> o, string key) =>
            o.TryGetValue(key, out var v) && v is bool b ? b : (bool?)null;

        /// <summary>An integer, as `as? Int` reads one: `1` and `1.0` both are.</summary>
        public static long? Integer(Dictionary<string, object?> o, string key)
        {
            if (!o.TryGetValue(key, out var v) || !(v is double d)) return null;
            if (Math.Floor(d) != d || double.IsInfinity(d)) return null;
            return (long)d;
        }

        private sealed class Reader
        {
            private readonly string s;
            private int i;

            public Reader(string text) { s = text; }

            public bool AtEnd => i >= s.Length;

            public void SkipSpace()
            {
                while (i < s.Length && (s[i] == ' ' || s[i] == '\t' || s[i] == '\n' || s[i] == '\r')) i++;
            }

            private char Peek() => i < s.Length ? s[i] : throw new FormatException("unexpected end");

            private void Expect(string word)
            {
                if (string.CompareOrdinal(s, i, word, 0, word.Length) != 0)
                    throw new FormatException("expected " + word);
                i += word.Length;
            }

            public object? Value()
            {
                switch (Peek())
                {
                    case '{': return ReadObject();
                    case '[': return ReadArray();
                    case '"': return ReadString();
                    case 't': Expect("true"); return true;
                    case 'f': Expect("false"); return false;
                    case 'n': Expect("null"); return null;
                    default: return ReadNumber();
                }
            }

            private Dictionary<string, object?> ReadObject()
            {
                var result = new Dictionary<string, object?>(StringComparer.Ordinal);
                i++;
                SkipSpace();
                if (Peek() == '}') { i++; return result; }
                while (true)
                {
                    SkipSpace();
                    if (Peek() != '"') throw new FormatException("expected a key");
                    var key = ReadString();
                    SkipSpace();
                    if (Peek() != ':') throw new FormatException("expected :");
                    i++;
                    SkipSpace();
                    result[key] = Value();
                    SkipSpace();
                    var c = Peek();
                    i++;
                    if (c == '}') return result;
                    if (c != ',') throw new FormatException("expected , or }");
                }
            }

            private List<object?> ReadArray()
            {
                var result = new List<object?>();
                i++;
                SkipSpace();
                if (Peek() == ']') { i++; return result; }
                while (true)
                {
                    SkipSpace();
                    result.Add(Value());
                    SkipSpace();
                    var c = Peek();
                    i++;
                    if (c == ']') return result;
                    if (c != ',') throw new FormatException("expected , or ]");
                }
            }

            private string ReadString()
            {
                i++;  // the opening quote
                var sb = new StringBuilder();
                while (true)
                {
                    var c = Peek();
                    i++;
                    if (c == '"') return sb.ToString();
                    if (c != '\\') { sb.Append(c); continue; }
                    var e = Peek();
                    i++;
                    switch (e)
                    {
                        case '"': sb.Append('"'); break;
                        case '\\': sb.Append('\\'); break;
                        case '/': sb.Append('/'); break;
                        case 'b': sb.Append('\b'); break;
                        case 'f': sb.Append('\f'); break;
                        case 'n': sb.Append('\n'); break;
                        case 'r': sb.Append('\r'); break;
                        case 't': sb.Append('\t'); break;
                        case 'u':
                            if (i + 4 > s.Length) throw new FormatException("short \\u escape");
                            sb.Append((char)int.Parse(s.Substring(i, 4), NumberStyles.HexNumber,
                                CultureInfo.InvariantCulture));
                            i += 4;
                            break;
                        default: throw new FormatException("bad escape");
                    }
                }
            }

            private double ReadNumber()
            {
                var start = i;
                while (i < s.Length && "+-0123456789.eE".IndexOf(s[i]) >= 0) i++;
                if (start == i) throw new FormatException("unexpected character");
                if (!double.TryParse(s.Substring(start, i - start), NumberStyles.Float,
                        CultureInfo.InvariantCulture, out var d))
                    throw new FormatException("bad number");
                return d;
            }
        }
    }
}
