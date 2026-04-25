class Agent {
  final String id;
  final String name;
  final String description;
  final String url;

  Agent({
    required this.id,
    this.name = '',
    required this.description,
    required this.url,
  });

  // Create a copy of this agent with updated fields
  Agent copyWith({
    String? id,
    String? name,
    String? description,
    String? url,
  }) {
    return Agent(
      id: id ?? this.id,
      name: name ?? this.name,
      description: description ?? this.description,
      url: url ?? this.url,
    );
  }

  // Convert Agent to JSON
  Map<String, dynamic> toJson() {
    return {
      'id': id,
      'name': name,
      'description': description,
      'url': url,
    };
  }

  // Create Agent from JSON
  factory Agent.fromJson(Map<String, dynamic> json) {
    return Agent(
      id: json['id'] as String,
      name: json['name'] as String? ?? '',
      description: json['description'] as String,
      url: json['url'] as String,
    );
  }
}
