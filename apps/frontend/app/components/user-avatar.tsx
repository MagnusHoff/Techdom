"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { updateUserAvatar } from "@/lib/api";
import type { AuthUser } from "@/lib/types";

const EMOJI_CHOICES = [
  "\u{1F600}",
  "\u{1F60E}",
  "\u{1F929}",
  "\u{1F916}",
  "\u{1F984}",
  "\u{1F419}",
  "\u{1F680}",
  "\u{1F525}",
  "\u{1F31F}",
  "\u{1F3AF}",
  "\u{1F389}",
  "\u{1F4A1}",
  "\u{1F340}",
  "\u{1F355}",
] as const;

const COLOR_CHOICES = [
  { value: "#F3F4F6", label: "Lys grå" },
  { value: "#1F2937", label: "Kullsvart" },
  { value: "#0EA5E9", label: "Safirblå" },
  { value: "#10B981", label: "Emerald-grønn" },
  { value: "#6366F1", label: "Indigoblå" },
  { value: "#F59E0B", label: "Gull-oransje" },
  { value: "#E5E7EB", label: "Offwhite" },
] as const;

const DARK_TEXT_COLORS = new Set(["#F3F4F6", "#E5E7EB"]);

interface UserEmojiAvatarProps {
  user: AuthUser | null;
  initials: string;
  avatarEmoji?: string | null;
  className?: string;
  label?: string;
  avatarColor?: string | null;
  onUserUpdate?: (user: AuthUser) => void;
}

export function UserEmojiAvatar({
  user,
  initials,
  avatarEmoji = null,
  avatarColor = null,
  className = "",
  label,
  onUserUpdate,
}: UserEmojiAvatarProps) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedEmoji, setSelectedEmoji] = useState<string | null>(avatarEmoji ?? null);
  const [selectedColor, setSelectedColor] = useState<string | null>(avatarColor ?? null);
  const avatarRef = useRef<HTMLButtonElement | null>(null);
  const pickerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setSelectedEmoji(avatarEmoji ?? null);
  }, [avatarEmoji]);

  useEffect(() => {
    setSelectedColor(avatarColor ?? null);
  }, [avatarColor]);

  useEffect(() => {
    if (!pickerOpen) {
      return;
    }
    const handleClick = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (!target) {
        return;
      }
      if (pickerRef.current?.contains(target)) {
        return;
      }
      if (avatarRef.current?.contains(target)) {
        return;
      }
      setPickerOpen(false);
    };
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setPickerOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [pickerOpen]);

  const displaySymbol = useMemo(() => selectedEmoji ?? initials, [selectedEmoji, initials]);

  const textColor = useMemo(() => {
    if (!selectedColor) {
      return undefined;
    }
    return DARK_TEXT_COLORS.has(selectedColor.toUpperCase()) ? "#0F172A" : "#FFFFFF";
  }, [selectedColor]);

  const wrapperStyle = useMemo(() => {
    if (!selectedColor) {
      return undefined;
    }
    return {
      backgroundColor: selectedColor,
      backgroundImage: "none",
    } as const;
  }, [selectedColor]);

  const ariaLabel = label ?? (selectedEmoji ? "Oppdater avatar" : "Velg emoji for avatar");
  const isInteractive = Boolean(user);

  const tryUpdateAvatar = async (emoji: string | null, color: string | null) => {
    if (!user || saving) {
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const updated = await updateUserAvatar({ avatarEmoji: emoji, avatarColor: color });
      setSelectedEmoji(updated.avatar_emoji ?? null);
      setSelectedColor(updated.avatar_color ?? null);
      onUserUpdate?.(updated);
      setPickerOpen(false);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Kunne ikke oppdatere avatar akkurat nå.";
      setError(message);
    } finally {
      setSaving(false);
    }
  };

  const handleSelectEmoji = (emoji: string) => {
    if (emoji === selectedEmoji) {
      setPickerOpen(false);
      return;
    }
    void tryUpdateAvatar(emoji, selectedColor);
  };

  const handleReset = () => {
    if (!selectedEmoji) {
      setPickerOpen(false);
      return;
    }
    void tryUpdateAvatar(null, selectedColor);
  };

  const handleSelectColor = (color: string) => {
    if (color === selectedColor) {
      setPickerOpen(false);
      return;
    }
    void tryUpdateAvatar(selectedEmoji, color);
  };

  const handleResetColor = () => {
    if (!selectedColor) {
      setPickerOpen(false);
      return;
    }
    void tryUpdateAvatar(selectedEmoji, null);
  };

  return (
    <div
      className={className ? `${className} avatar-picker` : "avatar-picker"}
      style={wrapperStyle}
    >
      <button
        ref={avatarRef}
        type="button"
        className="avatar-picker__button"
        aria-haspopup="true"
        aria-expanded={pickerOpen}
        onClick={() => {
          if (!isInteractive) {
            return;
          }
          setPickerOpen((open) => !open);
        }}
        aria-label={ariaLabel}
        disabled={!isInteractive || saving}
      >
        <span className="avatar-picker__symbol" aria-hidden="true" style={{ color: textColor }}>
          {displaySymbol}
        </span>
      </button>
      {pickerOpen ? (
        <div ref={pickerRef} className="avatar-picker__popover" role="menu">
          <div className="avatar-picker__intro">Velg emoji</div>
          <div className="avatar-picker__grid">
            {EMOJI_CHOICES.map((emoji) => (
              <button
                key={emoji}
                type="button"
                className={
                  emoji === selectedEmoji
                    ? "avatar-picker__emoji avatar-picker__emoji--active"
                    : "avatar-picker__emoji"
                }
                onClick={() => handleSelectEmoji(emoji)}
                aria-label={`Bruk ${emoji} som avatar`}
                disabled={saving}
              >
                <span aria-hidden="true">{emoji}</span>
              </button>
            ))}
          </div>
          <button
            type="button"
            className="avatar-picker__reset"
            onClick={handleReset}
            disabled={saving}
          >
            Tilbakestill til initialer
          </button>
          <hr className="avatar-picker__divider" />
          <div className="avatar-picker__intro">Velg bakgrunn</div>
          <div className="avatar-picker__colors">
            {COLOR_CHOICES.map((choice) => {
              const active = selectedColor === choice.value;
              return (
                <button
                  key={choice.value}
                  type="button"
                  className={
                    active
                      ? "avatar-picker__color avatar-picker__color--active"
                      : "avatar-picker__color"
                  }
                  onClick={() => handleSelectColor(choice.value)}
                  aria-label={`Bruk ${choice.label.toLowerCase()} som bakgrunn`}
                  style={{ backgroundColor: choice.value }}
                  disabled={saving}
                >
                  {active ? <span className="avatar-picker__color-indicator" /> : null}
                </button>
              );
            })}
          </div>
          <button
            type="button"
            className="avatar-picker__reset"
            onClick={handleResetColor}
            disabled={saving}
          >
            Tilbakestill bakgrunn
          </button>
          {error ? <p className="avatar-picker__error">{error}</p> : null}
        </div>
      ) : null}
    </div>
  );
}

export default UserEmojiAvatar;
