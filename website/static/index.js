function deleteNote(noteId) {
    fetch("/delete-note", {
        method: "POST",
        body: JSON.stringify({ noteId: noteId})
    }).then((_res)  => {
        window.location.href = "/";
    });
}

document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.dynamic-datetime').forEach(function(element) {
        const utcDateString = element.dataset.utcDatetime; // This will now be like "2023-10-27T14:30:00Z"
        if (utcDateString) {
            try {
                const dateObject = new Date(utcDateString); // Correctly parses as UTC
                const options = { 
                    year: 'numeric', month: 'short', day: 'numeric', 
                    hour: '2-digit', minute: '2-digit', hour12: true 
                };
                element.textContent = dateObject.toLocaleString(undefined, options); // Converts to browser's local time
            } catch (e) {
                console.error("Error formatting date:", e, "for date string:", utcDateString);
            }
        }
    });
    // ... other JS ...
});