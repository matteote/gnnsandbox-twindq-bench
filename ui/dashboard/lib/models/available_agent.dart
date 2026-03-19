class AvailableAgent {
  final String name;
  final String url;

  AvailableAgent({
    required this.name,
    required this.url,
  });

  // Create AvailableAgent from JSON
  factory AvailableAgent.fromJson(Map<String, dynamic> json) {
    return AvailableAgent(
      name: json['name'] as String? ?? '',
      url: json['url'] as String,
    );
  }

  // Convert AvailableAgent to JSON
  Map<String, dynamic> toJson() {
    return {
      'name': name,
      'url': url,
    };
  }
}