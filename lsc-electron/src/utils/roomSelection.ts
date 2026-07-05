export type ClickMode = 'normal' | 'toggle' | 'range'

export function resolveRoomSelection(
  currentSelected: Set<string>,
  currentRooms: Array<{ room_id: string }>,
  roomId: string,
  clickMode: ClickMode,
  lastIndex: number | null,
): Set<string> {
  if (clickMode === 'toggle') {
    const next = new Set(currentSelected)
    if (next.has(roomId)) {
      next.delete(roomId)
    } else {
      next.add(roomId)
    }
    return next
  } else if (clickMode === 'range' && lastIndex !== null) {
    const roomIndex = currentRooms.findIndex(r => r.room_id === roomId)
    if (roomIndex < 0) return currentSelected
    const start = Math.min(lastIndex, roomIndex)
    const end = Math.max(lastIndex, roomIndex)
    const rangeIds = currentRooms.slice(start, end + 1).map(r => r.room_id)
    return new Set(rangeIds)
  } else {
    if (currentSelected.has(roomId)) {
      if (currentSelected.size === 1) {
        return currentSelected
      }
      return new Set([roomId])
    } else {
      const next = new Set(currentSelected)
      next.add(roomId)
      return next
    }
  }
}
